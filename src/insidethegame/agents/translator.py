"""Translator: verified English lines into other languages, checked again.

A translation can introduce errors the English line did not have (a changed
number, a dropped or swapped name). So every translation is checked against
its verified English source:

- Numbers: the multiset of numbers must be identical. Arabic-Indic and
  Persian digits are normalized to 0-9 first, so either script passes.
- Names, Latin scripts: every player and club name in the source must
  appear as written, and no other squad name may appear.
- Names, Arabic: the translator is given a glossary from the name registry
  (names.py) and must use those spellings. The check looks for each
  glossary spelling after folding hamza and similar variants, and blocks any
  other squad name in Arabic that the source did not contain.
- Names, Japanese and Hindi: no map yet, so names are not checked.

A translation that fails, or a model that is unavailable, falls back to the
English line. The translator needs a model.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from .facts import MatchContext
from .names import LEAGUE, TRANSLITERATIONS, NameRegistry, normalize_script

log = logging.getLogger("insidethegame.agents")

LANGUAGES = {
    "ar": ("Arabic", False),
    "es": ("Spanish", True),
    "fr": ("French", True),
    "pt": ("Portuguese", True),
    "de": ("German", True),
    "sw": ("Swahili", True),
    "ja": ("Japanese", False),
    "hi": ("Hindi", False),
}  # code -> (name, uses Latin script)

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+(?:[.,٫]\d+)?")

TRANSLATOR_INSTRUCTIONS = """\
You translate live football commentary and match reports for fans.
Translate faithfully into each requested language, in natural broadcast style.

Rules you must follow:
- Keep every number exactly as written, using the digits 0-9, including
  decimals, scores like 3-1 and minutes like 90+2'.
- Do not add or remove any information.
- In Latin-script languages keep player and club names exactly as written.
- When a name glossary is given for a language, write those names exactly
  as the glossary spells them.
"""


@dataclass
class Translation:
    lang: str
    text: str
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _numbers(text: str) -> list[str]:
    normalized = text.translate(_DIGITS).replace("٫", ".")
    return sorted(n.replace(",", ".") for n in _NUM.findall(normalized))  # 0,35 means 0.35


def _names_in(text: str, names: list[str]) -> set[str]:
    found, rest = set(), text
    for n in sorted(names, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", rest):
            found.add(n)
            rest = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", " ", rest)
    return found


def _squad_names(ctx: MatchContext, registry: NameRegistry) -> list[str]:
    names = set()
    for p in registry.players:
        names |= {p.full, p.last}
    for full in ctx.player_names.values():
        names |= {full, full.split()[-1]}
    for aliases in registry.clubs.values():
        names |= set(aliases)
    names |= set(ctx.team_names.values())
    return sorted(names)


def check_translation(source: str, translated: str, lang: str, ctx: MatchContext,
                      registry: NameRegistry | None = None) -> list[str]:
    registry = registry or LEAGUE
    reasons = []
    if not translated.strip():
        return ["empty translation"]
    src, dst = _numbers(source), _numbers(translated)
    if src != dst:
        missing = sorted((Counter(src) - Counter(dst)).elements())
        extra = sorted((Counter(dst) - Counter(src)).elements())
        reasons.append("numbers changed" + (f"; missing {', '.join(missing)}" if missing else "")
                       + (f"; added {', '.join(extra)}" if extra else ""))

    squad = _squad_names(ctx, registry)
    in_source = _names_in(source, squad)
    latin = LANGUAGES.get(lang, (lang, True))[1]
    if latin:
        players = set(ctx.player_names.values()) | {p.full for p in registry.players}
        expected = {n.split()[-1] if n in players else n for n in in_source}  # surname is enough
        for n in sorted(expected):
            if not re.search(rf"(?<!\w){re.escape(n)}(?!\w)", translated):
                reasons.append(f"name '{n}' missing")
        for n in sorted(_names_in(translated, squad) - in_source):
            if not any(n in s for s in in_source):
                reasons.append(f"name '{n}' added")
    elif lang in TRANSLITERATIONS:
        table = TRANSLITERATIONS[lang]
        norm = normalize_script(translated, lang)
        # Each player's surname and each club's name must appear in the glossary spelling.
        required = set()
        for n in in_source:
            if n in ctx.player_names.values() or any(n == p.full for p in registry.players):
                required.add(n.split()[-1])
            elif n in table:
                required.add(n)
        for n in sorted(required):
            target = table.get(n)
            if target and normalize_script(target, lang) not in norm:
                reasons.append(f"name '{n}' missing (expected '{target}')")
        # Any other squad surname spelled in this script means a swapped name.
        source_surnames = {n.split()[-1] for n in in_source}
        for p in registry.players:
            target = table.get(p.last)
            if target and p.last not in source_surnames and re.search(
                    rf"(?<!\w){re.escape(normalize_script(target, lang))}(?!\w)", norm):
                reasons.append(f"name '{p.last}' added ('{target}')")
    return reasons


def _glossary_block(texts: list[str], langs: list[str], registry: NameRegistry) -> str:
    parts = []
    for lang in langs:
        g = {}
        for t in texts:
            g.update(registry.glossary(t, lang))
        if g:
            parts.append(f"Name glossary for {lang}: " + json.dumps(g, ensure_ascii=False))
    return "\n".join(parts)


class Translator:
    def __init__(self, client, registry: NameRegistry | None = None):
        from agent_framework import Agent

        self.registry = registry or LEAGUE
        self.agent = Agent(client=client, name="translator", instructions=TRANSLATOR_INSTRUCTIONS)

    async def translate(self, text: str, langs: list[str], ctx: MatchContext) -> dict[str, Translation]:
        return (await self.translate_many([text], langs, ctx))[0]

    async def translate_many(self, texts: list[str], langs: list[str],
                             ctx: MatchContext) -> list[dict[str, Translation]]:
        """Translate several texts in one model call; check each one."""
        langs = [l for l in langs if l != "en"]
        if not langs or not texts:
            return [{} for _ in texts]
        names = ", ".join(f"{c} ({LANGUAGES.get(c, (c,))[0]})" for c in langs)
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
        prompt = (f"Languages: {names}\n{_glossary_block(texts, langs, self.registry)}\n\n"
                  f"Texts:\n{numbered}\n\n"
                  'Return ONLY JSON: {"translations": [{"index": 0, "<language code>": "..."}]}, '
                  "one entry per text, same order.")
        error = None
        try:
            raw = (await self.agent.run(prompt)).text
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("translations", [])
        except Exception as e:  # noqa: BLE001 - any model failure falls back to English
            data, error = [], str(e)
            log.warning("translator: model unavailable (%s); English used", e)
        by_index = {}
        if isinstance(data, dict):  # single-text shape {"ar": "...", "es": "..."}
            data = [{"index": 0, **data}]
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                by_index[item["index"]] = item
        out = []
        for i, text in enumerate(texts):
            row = {}
            for lang in langs:
                if error:
                    row[lang] = Translation(lang, text, False, [f"model unavailable: {error}"])
                    continue
                t = str(by_index.get(i, {}).get(lang, ""))
                reasons = check_translation(text, t, lang, ctx, self.registry)
                row[lang] = Translation(lang=lang, text=t if not reasons else text, ok=not reasons, reasons=reasons)
            out.append(row)
        return out
