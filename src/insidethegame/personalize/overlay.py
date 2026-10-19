"""Overlay items and viewer personalization.

An OverlayItem is one thing the broadcast overlay shows: a ticker line, an
alert, a stats call-out or a player card line. The contract is documented in
docs/overlay.md and docs/overlay.schema.json.

Personalization never invents facts. It only selects, orders, times and
frames lines that already passed the FactChecker:
- casual mode: at most 20 words, no jargon, at most one number besides the
  score; lines that break this are swapped for the casual template line
- favourite club: that club's moments come first at the same instant, stay
  on screen longer, and are prefixed "Your team:" (no new names or numbers)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..agents.checker import Claim, FactChecker
from ..agents.facts import Fact, MatchContext
from ..agents.narrator import template_line

SCHEMA_VERSION = 1
MODES = ("analyst", "casual")
LANGS = ("en", "es")
JARGON = ("xg", "expected goals", "threat share", "final-third entries", "final third entries", "possession_id")
CASUAL_MAX_WORDS = 20
FAV_PREFIX = {"en": "Your team: ", "es": "Tu equipo: "}

SLOT_BY_KIND = {
    "goal": "alert", "yellow_card": "alert", "red_card": "alert",
    "half_time": "score", "full_time": "score",
    "momentum_swing": "stats",
    "player_card": "card",
}


@dataclass(frozen=True)
class ViewerProfile:
    mode: str = "analyst"                 # analyst | casual
    favourite_club: str | None = None     # team id from sim/teams.py
    focus_player: str | None = None       # player id
    lang: str = "en"                      # en | es

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if self.lang not in LANGS:
            raise ValueError(f"lang must be one of {LANGS}")


_SCORE = re.compile(r"\b\d+-\d+\b")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def word_count(text: str) -> int:
    return len(text.split())


def casual_violations(text: str) -> list[str]:
    reasons = []
    if word_count(text) > CASUAL_MAX_WORDS:
        reasons.append(f"casual style: {word_count(text)} words (max {CASUAL_MAX_WORDS})")
    low = text.lower()
    hits = [j for j in JARGON if re.search(rf"(?<![a-z]){re.escape(j)}(?![a-z])", low)]
    if hits:
        reasons.append(f"casual style: jargon ({', '.join(hits)})")
    numbers = _NUM.findall(_SCORE.sub(" ", text))
    if len(numbers) > 1:
        reasons.append(f"casual style: {len(numbers)} numbers besides the score (max 1)")
    return reasons


def show_for(kind: str, text: str, boost: bool = False) -> float:
    """Minimum on-screen time in wall-clock seconds, at any playback speed."""
    base = 6.0 if SLOT_BY_KIND.get(kind) == "alert" else max(4.0, round(word_count(text) * 0.35, 1))
    return base + (2.0 if boost else 0.0)


def make_item(*, match_id: str, profile: str, at_event: int, period: int, clock: float, display_clock: str,
              kind: str, text: str, translations: dict, fact_ids: list[str], evidence: list[int], source: str,
              blocked: list[str], favourite: str | None = None, boost: bool = False, slot: str | None = None) -> dict:
    slot = slot or SLOT_BY_KIND.get(kind, "ticker")
    return {
        "id": f"{match_id}:{profile}:{favourite or 'all'}:{slot}:{fact_ids[0] if fact_ids else at_event}",
        "match_id": match_id, "profile": profile, "lang": "en",
        "at_event": at_event, "period": period, "clock": clock, "display_clock": display_clock,
        "show_for_s": show_for(kind, text, boost), "slot": slot, "kind": kind, "text": text,
        "translations": {k: {"text": v["text"], "ok": bool(v["ok"])} for k, v in translations.items()},
        "fact_ids": list(fact_ids), "evidence": list(evidence), "source": source, "blocked": list(blocked),
        "boost": boost,
    }


def _untranslated(text: str, langs) -> dict:
    return {l: {"text": text, "ok": False} for l in langs}


def items_from_narration(rows: list[dict], facts: dict[str, Fact], ctx: MatchContext, profile: str,
                         langs: list[str]) -> list[dict]:
    """Narration lines (from itg-bake) -> OverlayItems, enforcing casual style."""
    items = []
    for r in rows:
        text, source, blocked, trans = r["text"], r["source"], list(r["blocked"]), dict(r.get("translations", {}))
        if profile == "casual":
            bad = casual_violations(text)
            if bad and r["fact_ids"] and r["fact_ids"][0] in facts:
                text = template_line(facts[r["fact_ids"][0]], ctx, "casual")
                source, blocked, trans = "template-fallback", blocked + bad, _untranslated(text, langs)
        for l in langs:
            trans.setdefault(l, {"text": text, "ok": False})
        items.append(make_item(match_id=ctx.match_id, profile=profile, at_event=r["at_event"], period=r["period"],
                               clock=r["clock"], display_clock=r["display_clock"], kind=r["kind"], text=text,
                               translations=trans, fact_ids=r["fact_ids"], evidence=r["evidence"], source=source,
                               blocked=blocked))
    return items


def apply_favourite(items: list[dict], facts: dict[str, Fact], favourite: str) -> list[dict]:
    """Frame and boost the favourite club's moments. Adds no names or numbers."""
    out = []
    for it in items:
        it = {**it, "translations": {k: dict(v) for k, v in it["translations"].items()}}
        fact = facts.get(it["fact_ids"][0]) if it["fact_ids"] else None
        ours = bool(fact and fact.teams and fact.teams[0] == favourite)
        if ours and it["slot"] != "score" and it["kind"] != "kickoff":  # neutral moments stay neutral
            text = FAV_PREFIX["en"] + it["text"]
            if it["profile"] == "casual" and casual_violations(text):
                text = it["text"]  # keep the casual limits; the boost still applies
            it["text"] = text
            for lang, t in it["translations"].items():
                if t["ok"] and text != t["text"]:
                    t["text"] = FAV_PREFIX.get(lang, "") + t["text"]
            it["boost"] = True
            it["show_for_s"] = show_for(it["kind"], it["text"], True)
        it["id"] = it["id"].replace(f":{it['profile']}:all:", f":{it['profile']}:{favourite}:")
        out.append(it)
    # Same instant: the favourite's moment first. Python's sort is stable, so order is otherwise kept.
    out.sort(key=lambda i: (i["period"], i["clock"], i["at_event"], not i["boost"]))
    return out


def verify_items(items: list[dict], facts: dict[str, Fact], ctx: MatchContext) -> list[str]:
    """Re-check every item with the FactChecker. Returns problems (empty means all verified)."""
    checker = FactChecker(facts, ctx)
    problems = []
    for it in items:
        v = checker.check(Claim(it["text"], it["fact_ids"]))
        if not v.ok:
            problems.append(f"{it['id']}: {'; '.join(v.reasons)}")
    return problems
