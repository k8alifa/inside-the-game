"""Translator: verified English lines into Spanish, checked again.

Output languages are English (primary) and Spanish. A translation can add
errors the English line did not have (a changed number, a dropped or swapped
name), so every translation is checked against its verified English source:

- Numbers: the multiset of numbers must be identical. A decimal comma
  (0,35) is read as a decimal point.
- Names: every player and club name in the source must appear exactly as
  written, and no other squad name may be added.

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
from .names import LEAGUE, NameRegistry
from .resilience import estimate_tokens

log = logging.getLogger("insidethegame.agents")

LANGUAGES = {"es": "Spanish"}  # English is the source language
SUPPORTED = ("en", *LANGUAGES)

_NUM = re.compile(r"\d+(?:[.,]\d+)?")

TRANSLATOR_INSTRUCTIONS = """\
You translate live football commentary and match reports for fans.
Translate faithfully into each requested language, in natural broadcast style.

Rules you must follow:
- Keep every number exactly as written, using the digits 0-9, including
  decimals, scores like 3-1 and minutes like 90+2'.
- Do not add or remove any information.
- Keep player and club names exactly as written. Never translate a name.
"""
# Prompt budget left for the texts after instructions, framing and headroom.
TEXT_TOKEN_BUDGET = 5000


def parse_langs(value: str) -> list[str]:
    """Comma-separated codes -> target languages (English dropped). Rejects anything unsupported."""
    codes = [c.strip().lower() for c in (value or "").split(",") if c.strip()]
    bad = [c for c in codes if c not in SUPPORTED]
    if bad:
        raise ValueError(f"unsupported language(s): {', '.join(bad)}; supported: {', '.join(SUPPORTED)}")
    return [c for c in codes if c != "en"]


@dataclass
class Translation:
    lang: str
    text: str
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _numbers(text: str) -> list[str]:
    return sorted(n.replace(",", ".") for n in _NUM.findall(text))


def _names_in(text: str, names: list[str]) -> set[str]:
    found, rest = set(), text
    for n in sorted(names, key=len, reverse=True):
        pattern = rf"(?<!\w){re.escape(n)}(?!\w)"
        if re.search(pattern, rest):
            found.add(n)
            rest = re.sub(pattern, " ", rest)
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
    if lang not in LANGUAGES:
        return [f"unsupported language '{lang}'"]
    if not translated.strip():
        return ["empty translation"]
    reasons = []
    src, dst = _numbers(source), _numbers(translated)
    if src != dst:
        missing = sorted((Counter(src) - Counter(dst)).elements())
        extra = sorted((Counter(dst) - Counter(src)).elements())
        reasons.append("numbers changed" + (f"; missing {', '.join(missing)}" if missing else "")
                       + (f"; added {', '.join(extra)}" if extra else ""))
    squad = _squad_names(ctx, registry)
    in_source = _names_in(source, squad)
    players = set(ctx.player_names.values()) | {p.full for p in registry.players}
    expected = {n.split()[-1] if n in players else n for n in in_source}  # surname is enough
    for n in sorted(expected):
        if not re.search(rf"(?<!\w){re.escape(n)}(?!\w)", translated):
            reasons.append(f"name '{n}' missing")
    for n in sorted(_names_in(translated, squad) - in_source):
        if not any(n in s for s in in_source):
            reasons.append(f"name '{n}' added")
    return reasons


def chunk_by_tokens(texts: list[str], max_items: int, budget: int = TEXT_TOKEN_BUDGET) -> list[list[int]]:
    """Split text indexes into batches that fit both an item cap and a token budget."""
    batches, cur, used = [], [], 0
    for i, t in enumerate(texts):
        cost = estimate_tokens(t) + 8
        if cur and (len(cur) >= max_items or used + cost > budget):
            batches.append(cur)
            cur, used = [], 0
        cur.append(i)
        used += cost
    if cur:
        batches.append(cur)
    return batches


class Translator:
    max_batch = 12

    def __init__(self, client, registry: NameRegistry | None = None):
        from agent_framework import Agent

        self.registry = registry or LEAGUE
        self.agent = Agent(client=client, name="translator", instructions=TRANSLATOR_INSTRUCTIONS)

    async def translate(self, text: str, langs: list[str], ctx: MatchContext) -> dict[str, Translation]:
        return (await self.translate_many([text], langs, ctx))[0]

    async def translate_many(self, texts: list[str], langs: list[str],
                             ctx: MatchContext) -> list[dict[str, Translation]]:
        """Translate several texts, in as few calls as the token budget allows; check each one."""
        langs = [l for l in langs if l != "en"]
        if not langs or not texts:
            return [{} for _ in texts]
        out: list[dict[str, Translation]] = [{} for _ in texts]
        for batch in chunk_by_tokens(texts, self.max_batch):
            rows = await self._translate_batch([texts[i] for i in batch], langs, ctx)
            for i, row in zip(batch, rows):
                out[i] = row
        return out

    async def _translate_batch(self, texts: list[str], langs: list[str], ctx: MatchContext):
        names = ", ".join(f"{c} ({LANGUAGES.get(c, c)})" for c in langs)
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
        prompt = (f"Languages: {names}\n\nTexts:\n{numbered}\n\n"
                  'Return ONLY JSON: {"translations": [{"index": 0, "<language code>": "..."}]}, '
                  "one entry per text, same order.")
        error = None
        try:
            raw = (await self.agent.run(prompt)).text
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("translations", [])
        except Exception as e:  # noqa: BLE001 - any model failure falls back to English
            data, error = [], str(e)
            log.warning("translator: model unavailable (%s); English used", e)
        if isinstance(data, dict):  # single-text shape {"es": "..."}
            data = [{"index": 0, **data}]
        by_index = {item["index"]: item for item in (data if isinstance(data, list) else [])
                    if isinstance(item, dict) and isinstance(item.get("index"), int)}
        rows = []
        for i, text in enumerate(texts):
            row = {}
            for lang in langs:
                if error:
                    row[lang] = Translation(lang, text, False, [f"model unavailable: {error}"])
                    continue
                t = str(by_index.get(i, {}).get(lang, ""))
                reasons = check_translation(text, t, lang, ctx, self.registry)
                row[lang] = Translation(lang=lang, text=t if not reasons else text, ok=not reasons, reasons=reasons)
            rows.append(row)
        return rows
