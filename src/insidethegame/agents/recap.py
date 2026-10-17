"""Recap writer: an automated, fact-checked match report.

Reuses the analyst workflow (draft -> fact-check -> one revision round) with
a different writer. Each paragraph is a claim that cites facts, so the same
checker guards the recap. Paragraphs that still fail are dropped, never shown.
"""

from __future__ import annotations

from .analyst import LLMAnalyst
from .checker import Claim, Verdict
from .facts import Fact, MatchContext
from .workflow import Report, analyze

RECAP_INSTRUCTIONS = """\
You write the automated post-match report for a football broadcaster's app.

Structure:
- order 0: a headline, at most 12 words, kind "headline"
- order 1: a lede that gives the result and the story of the match
- orders 2 and up: paragraphs in match order: goals, turning points, the
  standout player, what the numbers say about the result

Rules you must follow:
- Use ONLY the facts provided. Every item cites the fact ids it relies on.
- Every number must appear in a cited fact's figures. Do not compute new ones.
- Only name players and teams that appear in the facts you cite.
- Two or three sentences per paragraph. Plain English, no cliches.

Return ONLY JSON:
{"claims": [{"text": "...", "fact_ids": ["..."], "kind": "headline|paragraph", "order": 0}]}
"""


class LLMRecapWriter(LLMAnalyst):
    instructions = RECAP_INSTRUCTIONS
    agent_name = "recap_writer"

    def task(self, ctx: MatchContext, n: int) -> str:
        return f"Write the match report: one headline and up to {n - 1} paragraphs."


class TemplateRecapWriter:
    """Offline stand-in: a recap assembled from fact summaries, in match order."""

    async def draft(self, facts: dict[str, Fact], ctx: MatchContext, n: int = 8) -> list[Claim]:
        home, away = ctx.team_names[ctx.home], ctx.team_names[ctx.away]
        final = facts["final-score"]
        h, a = final.figures["home_goals"], final.figures["away_goals"]
        if h == a:
            headline = f"{home} and {away} draw {h}-{a}"
        else:
            winner, loser = (home, away) if h > a else (away, home)
            headline = f"{winner} beat {loser} {max(h, a)}-{min(h, a)}"
        items = [Claim(headline, ["final-score"], "headline", 0)]

        xg_ids = [f"xg-story-{ctx.home}", f"xg-story-{ctx.away}"]
        lede = " ".join([final.summary] + [facts[i].summary for i in xg_ids])
        items.append(Claim(lede, ["final-score", *xg_ids], "paragraph", 1))

        timeline = sorted(
            (f for f in facts.values() if f.kind in ("goal", "momentum_swing", "red_card")),
            key=lambda f: (f.minute or 0, f.kind != "momentum_swing"),
        )
        for f in timeline:
            items.append(Claim(f.summary, [f.id], "paragraph", len(items)))

        for fid in ("top-threat", f"keeper-{ctx.home}", f"keeper-{ctx.away}", f"press-{ctx.home}", f"press-{ctx.away}"):
            if fid in facts and len(items) < n:
                items.append(Claim(facts[fid].summary, [fid], "paragraph", len(items)))
        return items[: max(n, 2)]

    async def revise(self, facts, ctx, rejected: list[Verdict]):
        return []


def render_markdown(report: Report, ctx: MatchContext) -> str:
    """Headline, then paragraphs in order, each with its evidence trail."""
    items = sorted(report.verified, key=lambda v: (v.claim.order if v.claim.order is not None else 999))
    out = []
    head = next((v for v in items if v.claim.kind == "headline"), None)
    out.append(f"# {head.claim.text if head else ctx.team_names[ctx.home] + ' vs ' + ctx.team_names[ctx.away]}\n")
    notes = []
    for v in items:
        if v is head:
            continue
        notes.append(v)
        out.append(f"{v.claim.text} [{len(notes)}]\n")
    out.append("---\n**Evidence**\n")
    for i, v in enumerate(notes, 1):
        ev = ", ".join(map(str, v.evidence[:10])) + (" ..." if len(v.evidence) > 10 else "")
        out.append(f"{i}. facts: {', '.join(v.claim.fact_ids)}; events: {ev or 'all events'}")
    if report.rejected:
        out.append(f"\n_{len(report.rejected)} draft item(s) were blocked by the fact-checker._")
    return "\n".join(out) + "\n"


async def write_recap(events: list, ctx: MatchContext, writer=None, n: int = 8, max_rounds: int = 2) -> Report:
    return await analyze(events, ctx, writer or TemplateRecapWriter(), n_claims=n, max_rounds=max_rounds)
