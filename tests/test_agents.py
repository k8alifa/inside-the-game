import asyncio
import json
from pathlib import Path

import pytest
from agent_framework import BaseChatClient, ChatResponse, Message

from insidethegame.agents.analyst import LLMAnalyst, TemplateAnalyst, parse_claims
from insidethegame.agents.checker import Claim, FactChecker
from insidethegame.agents.facts import build_facts, load_match
from insidethegame.agents.workflow import analyze

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "NBA-HCF-7"


@pytest.fixture(scope="module")
def match():
    return load_match(SAMPLE)


@pytest.fixture(scope="module")
def checker(match):
    events, ctx = match
    return FactChecker(build_facts(events, ctx), ctx)


class ScriptedChatClient(BaseChatClient):
    """Stand-in model: returns canned replies in order, records prompts."""

    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.prompts = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.prompts.append(messages[-1].text)
        return ChatResponse(messages=[Message(role="assistant", contents=[self.replies.pop(0)])])


# ---------------------------------------------------------------- facts
def test_facts_have_evidence_that_exists(match):
    events, ctx = match
    ids = {e.event_id for e in events}
    facts = build_facts(events, ctx)
    assert "final-score" in facts and facts["final-score"].figures == {"home_goals": 3, "away_goals": 1}
    for f in facts.values():
        assert set(f.evidence) <= ids, f.id


def test_goal_facts_match_events(match):
    events, ctx = match
    facts = build_facts(events, ctx)
    goals = [e for e in events if e.type == "goal"]
    goal_facts = [f for f in facts.values() if f.kind == "goal"]
    assert len(goal_facts) == len(goals)
    for f, g in zip(goal_facts, goals):
        assert g.event_id in f.evidence and g.player in f.players


# -------------------------------------------------------------- checker
@pytest.mark.parametrize("text, ids", [
    ("Finn Galbraith's 14th-minute opener came from a chance worth just 0.01 xG.", ["goal-1"]),
    ("Northbridge won 3-1.", ["final-score"]),
    ("Haldane settled it at 90+2' with a 0.35 xG chance.", ["goal-4"]),
])
def test_checker_accepts_supported_claims(checker, text, ids):
    assert checker.check(Claim(text, ids)).ok


@pytest.mark.parametrize("text, ids, reason", [
    ("Galbraith scored twice.", ["goal-1"], "number 'twice'"),
    ("Northbridge had 70% possession.", ["stats-NBA"], "number '70'"),
    ("Kai Haldane won it late.", ["goal-1"], "player 'Kai Haldane'"),
    ("Elmstead Rovers were watching.", ["final-score"], "team 'Elmstead Rovers'"),
    ("A made-up fact.", ["goal-99"], "unknown fact ids"),
    ("No citation at all.", [], "cites no facts"),
])
def test_checker_rejects_unsupported_claims(checker, text, ids, reason):
    v = checker.check(Claim(text, ids))
    assert not v.ok
    assert any(reason in r for r in v.reasons), v.reasons


# ------------------------------------------------------------- workflow
def test_template_workflow_verifies_everything(match):
    events, ctx = match
    report = asyncio.run(analyze(events, ctx, TemplateAnalyst(), n_claims=8))
    assert len(report.verified) == 8 and not report.rejected and report.rounds == 1
    assert all(v.evidence for v in report.verified if v.claim.fact_ids != ["stats-NBA"])


def test_llm_workflow_revises_rejected_claims(match):
    """Round 1 has a hallucinated stat; the checker sends it back; round 2 fixes it."""
    events, ctx = match
    round1 = {"claims": [
        {"text": "Northbridge won 3-1.", "fact_ids": ["final-score"]},
        {"text": "Galbraith scored twice from 0.01 xG.", "fact_ids": ["goal-1"]},
    ]}
    round2 = {"claims": [
        {"text": "Galbraith's opener came from just 0.01 xG.", "fact_ids": ["goal-1"]},
    ]}
    client = ScriptedChatClient([json.dumps(round1), "```json\n" + json.dumps(round2) + "\n```"])
    report = asyncio.run(analyze(events, ctx, LLMAnalyst(client), max_rounds=2))
    assert report.rounds == 2
    assert [v.claim.text for v in report.verified] == [
        "Northbridge won 3-1.", "Galbraith's opener came from just 0.01 xG."]
    assert [v.claim.text for v in report.rejected] == ["Galbraith scored twice from 0.01 xG."]
    assert "goal-1" in client.prompts[0] and "rejected" in client.prompts[1]


def test_llm_workflow_stops_after_max_rounds(match):
    events, ctx = match
    bad = json.dumps({"claims": [{"text": "Northbridge had 70% possession.", "fact_ids": ["stats-NBA"]}]})
    report = asyncio.run(analyze(events, ctx, LLMAnalyst(ScriptedChatClient([bad, bad])), max_rounds=2))
    assert report.rounds == 2 and not report.verified and len(report.rejected) == 2


def test_parse_claims_tolerates_noise():
    assert parse_claims("Sure! ```json\n{\"claims\": [{\"text\": \"x\", \"fact_ids\": [\"a\"]}]}\n```")[0].fact_ids == ["a"]
    assert parse_claims("no json here") == []
