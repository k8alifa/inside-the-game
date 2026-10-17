"""Translator: verified English lines into other languages, checked again.

A translation can introduce errors the English line did not have (a changed
number, a dropped or swapped name). So every translation is checked against
its verified English source:

- Numbers: the multiset of numbers must be identical. Arabic-Indic and
  Persian digits are normalized to 0-9 first, so either script passes.
- Names: in Latin-script languages, every squad surname in the source must
  appear in the translation. Non-Latin scripts transliterate names, so this
  check is skipped there and the number check still applies.

A translation that fails is not shown; the UI falls back to the English line.
The translator needs a model. With no model configured it is disabled.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from .facts import MatchContext

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
- Keep player and club names as written. In non-Latin scripts you may
  transliterate names.

Return ONLY JSON: {"translations": {"<language code>": "..."}}
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
    # 0,35 in some languages means 0.35
    return sorted(n.replace(",", ".") for n in _NUM.findall(normalized))


def check_translation(source: str, translated: str, lang: str, ctx: MatchContext) -> list[str]:
    reasons = []
    if not translated.strip():
        return ["empty translation"]
    src, dst = _numbers(source), _numbers(translated)
    if src != dst:
        missing = sorted((Counter(src) - Counter(dst)).elements())
        extra = sorted((Counter(dst) - Counter(src)).elements())
        reasons.append("numbers changed" + (f"; missing {', '.join(missing)}" if missing else "")
                       + (f"; added {', '.join(extra)}" if extra else ""))
    latin = LANGUAGES.get(lang, (lang, True))[1]
    if latin:
        for full in ctx.player_names.values():
            surname = full.split()[-1]
            if re.search(rf"\b{re.escape(surname)}\b", source) and surname not in translated:
                reasons.append(f"name '{surname}' missing")
    return reasons


class Translator:
    def __init__(self, client):
        from agent_framework import Agent

        self.agent = Agent(client=client, name="translator", instructions=TRANSLATOR_INSTRUCTIONS)

    async def translate(self, text: str, langs: list[str], ctx: MatchContext) -> dict[str, Translation]:
        names = ", ".join(f"{c} ({LANGUAGES.get(c, (c,))[0]})" for c in langs)
        resp = await self.agent.run(f"Languages: {names}\n\nText:\n{text}")
        raw = resp.text
        try:
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("translations", {})
        except (json.JSONDecodeError, ValueError):
            data = {}
        out = {}
        for lang in langs:
            t = str(data.get(lang, ""))
            reasons = check_translation(text, t, lang, ctx)
            out[lang] = Translation(lang=lang, text=t if not reasons else text, ok=not reasons, reasons=reasons)
        return out
