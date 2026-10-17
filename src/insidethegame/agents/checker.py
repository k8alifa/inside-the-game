"""Fact-checker: blocks any claim the data does not support.

The checker never trusts the analyst. It rebuilds the fact registry from the
raw events, then for each claim:

1. Every cited fact id must exist.
2. Every number in the text (digits, number words two to twenty, and count
   words like "twice", "brace", "hat-trick") must
   match a figure in a cited fact, at the precision written. "1.4" matches
   1.38; "2" does not match 2.6.
3. Every squad player named in the text must be involved in a cited fact.
4. Every team named in the text must play in this match.

A claim that fails any check is rejected with reasons, so a retry can fix it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .facts import Fact, MatchContext

_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
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

    def to_dict(self) -> dict:
        return {"text": self.text, "fact_ids": list(self.fact_ids), "kind": self.kind}


@dataclass
class Verdict:
    claim: Claim
    ok: bool
    reasons: list[str] = field(default_factory=list)
    evidence: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**self.claim.to_dict(), "verified": self.ok, "reasons": self.reasons, "evidence": self.evidence}


def _numbers_in(text: str) -> list[tuple[str, float]]:
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
    def __init__(self, facts: dict[str, Fact], ctx: MatchContext):
        self.facts = facts
        self.ctx = ctx
        # Match the longest names first so "Finn Galbraith" wins over "Galbraith".
        names = []
        for pid, full in ctx.player_names.items():
            names.append((full, pid))
            names.append((full.split()[-1], pid))
        self._names = sorted(names, key=lambda x: -len(x[0]))
        from ..sim.teams import TEAMS
        self._all_teams = {t.id: t.name for t in TEAMS.values()} | dict(ctx.team_names)
        self._surname_owners: dict[str, set[str]] = {}
        for pid, full in ctx.player_names.items():
            self._surname_owners.setdefault(full.split()[-1], set()).add(pid)

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

        involved = {p for f in cited for p in f.players}
        text = claim.text
        for name, pid in self._names:
            if re.search(rf"\b{re.escape(name)}\b", text):
                owners = self._surname_owners.get(name, {pid})
                if not (owners & involved) and pid not in involved:
                    reasons.append(f"player '{name}' is not involved in the cited facts")
                text = re.sub(rf"\b{re.escape(name)}\b", " ", text)

        for tid, tname in self._all_teams.items():
            if tid not in self.ctx.team_names and (tname.lower() in claim.text.lower()):
                reasons.append(f"team '{tname}' did not play in this match")

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
