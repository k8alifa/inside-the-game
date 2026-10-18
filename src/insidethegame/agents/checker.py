"""Fact-checker: blocks any claim the data does not support.

The checker never trusts the analyst. It rebuilds the fact registry from the
raw events, then for each claim:

1. Every cited fact id must exist.
2. Every number in the text (digits, number words one to twenty, and count
   words like "twice", "brace", "hat-trick") must
   match a figure in a cited fact, at the precision written. "1.4" matches
   1.38; "2" does not match 2.6.
3. Every player named in the text, by full name, surname alone, or first
   name alone, must be involved in a cited fact. Players from other clubs in
   the league are blocked as not in this match.
4. Every club named in the text, by full or short name, must play in this
   match.

A claim that fails any check is rejected with reasons, so a retry can fix it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .facts import Fact, MatchContext
from .names import LEAGUE, NameRegistry

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twice": 2, "thrice": 3, "brace": 2, "hat-trick": 3, "hat trick": 3,
}
_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)")
_WORD_RE = re.compile(r"\b(" + "|".join(map(re.escape, _NUMBER_WORDS)) + r")\b", re.IGNORECASE)


@dataclass
class Claim:
    text: str
    fact_ids: list[str]
    kind: str = "insight"
    order: int | None = None  # position in a longer piece, e.g. a recap
    source: str = "model"     # "model", "template", or "template-fallback"

    def to_dict(self) -> dict:
        return {"text": self.text, "fact_ids": list(self.fact_ids), "kind": self.kind, "order": self.order,
                "source": self.source}


@dataclass
class Verdict:
    claim: Claim
    ok: bool
    reasons: list[str] = field(default_factory=list)
    evidence: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**self.claim.to_dict(), "verified": self.ok, "reasons": self.reasons, "evidence": self.evidence}


# Phrases where "one" is not a count.
_ONE_IDIOMS = re.compile(r"\b(no one|one of|one-two|one-on-one|one another|the one)\b", re.IGNORECASE)


def _numbers_in(text: str) -> list[tuple[str, float]]:
    text = _ONE_IDIOMS.sub(" ", text)
    out = [(m.group(1), float(m.group(1))) for m in _NUM_RE.finditer(text)]
    out += [(m.group(1), float(_NUMBER_WORDS[m.group(1).lower()])) for m in _WORD_RE.finditer(text)]
    return out


def _allowed_values(facts: list[Fact]) -> list[float]:
    vals: list[float] = []
    for f in facts:
        for v in f.figures.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
            elif isinstance(v, str):
                vals += [float(n) for n in re.findall(r"\d+(?:\.\d+)?", v)]
        if f.minute is not None:
            vals += [float(f.minute), float(f.minute + 1)]
    return vals


def _matches(token: str, value: float, allowed: list[float]) -> bool:
    decimals = len(token.split(".")[1]) if "." in token else 0
    return any(round(a, decimals) == round(value, decimals) for a in allowed)


class FactChecker:
    def __init__(self, facts: dict[str, Fact], ctx: MatchContext, registry: NameRegistry | None = None):
        self.facts = facts
        self.ctx = ctx
        self.registry = registry or LEAGUE
        in_match = set(ctx.player_names)
        # (text, kind, candidate player ids), longest first so full names win over parts.
        by_text: dict[tuple[str, str], set[str]] = {}
        for p in self.registry.players:
            for text, kind in ((p.full, "name"), (p.last, "surname"), (p.first, "first name")):
                by_text.setdefault((text, kind), set()).add(p.id)
        for pid, full in ctx.player_names.items():  # squads not in the league registry
            if not any(pid in ids for ids in by_text.values()):
                first, _, last = full.partition(" ")
                for text, kind in ((full, "name"), (last or full, "surname"), (first, "first name")):
                    by_text.setdefault((text, kind), set()).add(pid)
        self._names = sorted(((t, k, ids) for (t, k), ids in by_text.items()), key=lambda x: -len(x[0]))
        self._in_match = in_match
        clubs = []
        for tid, aliases in self.registry.clubs.items():
            clubs += [(a, tid) for a in aliases]
        for tid, name in ctx.team_names.items():
            clubs.append((name, tid))
        self._clubs = sorted(set(clubs), key=lambda x: -len(x[0]))

    def check(self, claim: Claim) -> Verdict:
        reasons: list[str] = []
        if not claim.fact_ids:
            reasons.append("cites no facts")
        unknown = [f for f in claim.fact_ids if f not in self.facts]
        if unknown:
            reasons.append(f"unknown fact ids: {', '.join(unknown)}")
        cited = [self.facts[f] for f in claim.fact_ids if f in self.facts]

        allowed = _allowed_values(cited)
        for token, value in _numbers_in(claim.text):
            if not _matches(token, value, allowed):
                reasons.append(f"number '{token}' is not supported by the cited facts")

        # Clubs first, so club names are not misread as player names.
        text = claim.text
        for alias, tid in self._clubs:
            pattern = rf"\b{re.escape(alias)}\b"
            if re.search(pattern, text, re.IGNORECASE):
                if tid not in self.ctx.team_names:
                    reasons.append(f"team '{alias}' did not play in this match")
                text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

        involved = {p for f in cited for p in f.players}
        for name, kind, ids in self._names:
            pattern = rf"\b{re.escape(name)}\b"
            if not re.search(pattern, text):
                continue
            if not ids & involved:
                if ids & self._in_match:
                    reasons.append(f"player '{name}' ({kind}) is not involved in the cited facts")
                else:
                    reasons.append(f"player '{name}' ({kind}) is not in this match")
            text = re.sub(pattern, " ", text)

        evidence = sorted({e for f in cited for e in f.evidence})
        return Verdict(claim=claim, ok=not reasons, reasons=_dedupe(reasons), evidence=evidence)

    def check_all(self, claims: list[Claim]) -> list[Verdict]:
        return [self.check(c) for c in claims]


def _dedupe(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
