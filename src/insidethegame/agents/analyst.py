"""Analyst agent: turns the fact registry into readable insights.

Two backends share one interface:

- TemplateAnalyst: offline stand-in, no model. Picks the most important facts
  and phrases them from their summaries. Used for tests and when no model is
  configured.
- LLMAnalyst: a Microsoft Agent Framework agent. It sees a catalog of fact ids
  and summaries, can call a tool for full figures, and must return JSON
  claims that cite fact ids. Rejected claims come back with reasons for one
  revision round.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from .checker import Claim, Verdict
from .facts import Fact, MatchContext

log = logging.getLogger("insidethegame.agents")

PROFILES = {
    "analyst": "Audience: studio analysts and engaged fans. Tactical and precise; use the key numbers.",
    "casual": ("Audience: casual fans. Warm, simple words, short sentences. Use at most one number per "
               "sentence and avoid jargon such as xG."),
}

ANALYST_INSTRUCTIONS = """\
You are the match analyst for a football broadcast. You explain what happened
and why, for fans and studio presenters.

Rules you must follow:
- Use ONLY the facts provided. Every claim cites the fact ids it relies on.
- Every number you write must appear in a cited fact's figures. Do not
  compute new numbers, do not estimate, do not round in a misleading way.
- Only name players and teams that appear in the facts you cite.
- Prefer insight over description: connect facts (e.g. a momentum swing and
  the goal that followed), but cite every fact you connect.
- Plain English, one or two sentences per claim, broadcast tone.

Return ONLY JSON in this shape, nothing else:
{"claims": [{"text": "...", "fact_ids": ["..."], "kind": "..."}]}
"""


class AnalystBackend(Protocol):
    async def draft(self, facts: dict[str, Fact], ctx: MatchContext, n: int) -> list[Claim]: ...

    async def revise(self, facts: dict[str, Fact], ctx: MatchContext, rejected: list[Verdict]) -> list[Claim]: ...


class TemplateAnalyst:
    """Deterministic stand-in: the top-n facts by importance, as written."""

    def __init__(self, profile: str = "analyst"):
        self.profile = profile

    async def draft(self, facts: dict[str, Fact], ctx: MatchContext, n: int = 8) -> list[Claim]:
        ranked = sorted(facts.values(), key=lambda f: (-f.importance, f.id))
        # Variety: goals and the result always qualify; other kinds once each, then fill.
        picked = [f for f in ranked if f.kind in ("result", "goal")]
        seen = {f.kind for f in picked}
        for f in ranked:
            if f.kind not in seen:
                picked.append(f)
                seen.add(f.kind)
        picked += [f for f in ranked if f not in picked]
        picked = sorted(picked[:n], key=lambda f: (-f.importance, f.id))
        return [Claim(text=f.summary, fact_ids=[f.id], kind=f.kind, source="template") for f in picked]

    async def revise(self, facts, ctx, rejected):
        return []  # template claims are built from facts; nothing to fix


def fact_catalog(facts: dict[str, Fact]) -> str:
    rows = sorted(facts.values(), key=lambda f: (-f.importance, f.id))
    return "\n".join(f"- {f.id} [{f.kind}]: {f.summary}" for f in rows)


def parse_claims(text: str) -> list[Claim]:
    """Pull the JSON object out of a model reply, tolerating code fences."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    claims = []
    for c in data.get("claims", []):
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            ids = c.get("fact_ids") or []
            order = c.get("order")
            claims.append(Claim(text=c["text"].strip(), fact_ids=[str(i) for i in ids],
                                kind=str(c.get("kind", "insight")),
                                order=order if isinstance(order, int) else None))
    return claims


class LLMAnalyst:
    instructions = ANALYST_INSTRUCTIONS
    agent_name = "analyst"
    fallback_cls = TemplateAnalyst

    def task(self, ctx: MatchContext, n: int) -> str:
        return f"Write the {n} most important insights about this match."

    def __init__(self, client, profile: str = "analyst"):
        from agent_framework import Agent

        self._facts: dict[str, Fact] = {}
        self.profile = profile
        self.fallback = self.fallback_cls(profile)
        self.fallback_reason: str | None = None

        def get_fact_details(fact_ids: list[str]) -> str:
            """Return figures, players and evidence event ids for the given fact ids."""
            return json.dumps({i: self._facts[i].to_dict() for i in fact_ids if i in self._facts})

        instructions = self.instructions + "\n" + PROFILES.get(profile, PROFILES["analyst"])
        self.agent = Agent(client=client, name=self.agent_name, instructions=instructions,
                           tools=[get_fact_details])
        self.session = None

    async def draft(self, facts: dict[str, Fact], ctx: MatchContext, n: int = 8) -> list[Claim]:
        self._facts = facts
        self.session = self.agent.create_session()
        prompt = (
            f"Match: {ctx.team_names[ctx.home]} (home) vs {ctx.team_names[ctx.away]} (away).\n"
            f"{self.task(ctx, n)}\n\nFacts:\n{fact_catalog(facts)}"
        )
        try:
            resp = await self.agent.run(prompt, session=self.session)
            claims = parse_claims(resp.text)
            if not claims:
                raise ValueError("model reply had no parseable claims")
            return claims
        except Exception as e:  # noqa: BLE001 - any model failure falls back to the template
            self.fallback_reason = str(e)
            log.warning("%s: model unavailable (%s); source=template-fallback", self.agent_name, e)
            claims = await self.fallback.draft(facts, ctx, n)
            for c in claims:
                c.source = "template-fallback"
            return claims

    async def revise(self, facts: dict[str, Fact], ctx: MatchContext, rejected: list[Verdict]) -> list[Claim]:
        if self.fallback_reason:
            return []
        self._facts = facts
        lines = [f"- [kind {v.claim.kind}, order {v.claim.order}] \"{v.claim.text}\" "
                 f"(cited {', '.join(v.claim.fact_ids) or 'nothing'}): "
                 + "; ".join(v.reasons) for v in rejected]
        prompt = (
            "The fact-checker rejected these claims:\n" + "\n".join(lines)
            + "\n\nRewrite each so it is fully supported by the facts it cites, or drop it. "
            "Keep each claim's kind and order. Return ONLY the rewritten claims in the same JSON shape."
        )
        try:
            resp = await self.agent.run(prompt, session=self.session)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: revision skipped, model unavailable (%s)", self.agent_name, e)
            return []
        return parse_claims(resp.text)
