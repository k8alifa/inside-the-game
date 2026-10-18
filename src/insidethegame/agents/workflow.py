"""Analyst <-> fact-checker workflow on Microsoft Agent Framework.

    MatchBrief -> [analyst] -> Draft -> [fact_checker] -> Report (output)
                      ^                      |
                      +----- Revision -------+   (rejected claims, max N rounds)

Only verified claims reach the output. Rejected claims are kept in the report
with the checker's reasons, so the audit trail is visible in the demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from typing_extensions import Never

from .analyst import AnalystBackend, TemplateAnalyst
from .checker import Claim, FactChecker, Verdict
from .facts import Fact, MatchContext, build_facts


@dataclass
class MatchBrief:
    events: list
    ctx: MatchContext
    n_claims: int = 8


@dataclass
class Draft:
    claims: list[Claim]
    round: int


@dataclass
class Revision:
    rejected: list[Verdict]
    round: int


@dataclass
class Report:
    match_id: str
    verified: list[Verdict] = field(default_factory=list)
    rejected: list[Verdict] = field(default_factory=list)
    rounds: int = 1

    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "rounds": self.rounds,
            "verified": [v.to_dict() for v in self.verified],
            "rejected": [v.to_dict() for v in self.rejected],
        }


class AnalystExecutor(Executor):
    def __init__(self, backend: AnalystBackend):
        super().__init__(id="analyst")
        self.backend = backend
        self.facts: dict[str, Fact] = {}
        self.ctx: MatchContext | None = None

    @handler
    async def on_brief(self, brief: MatchBrief, ctx: WorkflowContext[Draft]) -> None:
        self.ctx = brief.ctx
        self.facts = build_facts(brief.events, brief.ctx)
        claims = await self.backend.draft(self.facts, brief.ctx, brief.n_claims)
        await ctx.send_message(Draft(claims=claims, round=1))

    @handler
    async def on_revision(self, rev: Revision, ctx: WorkflowContext[Draft]) -> None:
        claims = await self.backend.revise(self.facts, self.ctx, rev.rejected)
        await ctx.send_message(Draft(claims=claims, round=rev.round + 1))


class FactCheckerExecutor(Executor):
    """Independent of the analyst: rebuilds facts from the raw events itself."""

    def __init__(self, events: list, match_ctx: MatchContext, max_rounds: int = 2):
        super().__init__(id="fact_checker")
        self.checker = FactChecker(build_facts(events, match_ctx), match_ctx)
        self.report = Report(match_id=match_ctx.match_id)
        self.max_rounds = max_rounds

    @handler
    async def on_draft(self, draft: Draft, ctx: WorkflowContext[Revision, Report]) -> None:
        verdicts = self.checker.check_all(draft.claims)
        self.report.rounds = draft.round
        self.report.verified += [v for v in verdicts if v.ok]
        failed = [v for v in verdicts if not v.ok]
        if failed and draft.round < self.max_rounds:
            self.report.rejected += failed
            await ctx.send_message(Revision(rejected=failed, round=draft.round))
            return
        self.report.rejected += failed
        await ctx.yield_output(self.report)


def build_workflow(events: list, match_ctx: MatchContext, backend: AnalystBackend | None = None,
                   max_rounds: int = 2):
    analyst = AnalystExecutor(backend or TemplateAnalyst())
    checker = FactCheckerExecutor(events, match_ctx, max_rounds=max_rounds)
    return (
        WorkflowBuilder(name="analyst-factchecker", start_executor=analyst)
        .add_edge(analyst, checker)
        .add_edge(checker, analyst)
        .build()
    )


async def analyze(events: list, match_ctx: MatchContext, backend: AnalystBackend | None = None,
                  n_claims: int = 8, max_rounds: int = 2) -> Report:
    wf = build_workflow(events, match_ctx, backend, max_rounds)
    result = await wf.run(MatchBrief(events=events, ctx=match_ctx, n_claims=n_claims))
    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError("workflow finished without a report")
    return outputs[-1]
