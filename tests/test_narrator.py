import asyncio
import json
from pathlib import Path

import pytest

from insidethegame.agents.facts import load_match
from insidethegame.agents.narrator import LLMNarrator, MomentDetector, narrate
from test_agents import ScriptedChatClient

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "NBA-HCF-7"


@pytest.fixture(scope="module")
def match():
    return load_match(SAMPLE)


def collect(events, ctx, narrator=None):
    async def run():
        return [line async for line in narrate(events, ctx, narrator)]
    return asyncio.run(run())


def test_template_lines_cover_the_match(match):
    events, ctx = match
    lines = collect(events, ctx)
    kinds = [l.kind for l in lines]
    assert kinds[0] == "kickoff" and kinds[-1] == "full_time" and "half_time" in kinds
    assert kinds.count("goal") == sum(e.type == "goal" for e in events)
    assert all(l.source == "template" and not l.blocked for l in lines)
    assert "3-1" in lines[-1].text


def test_detector_only_uses_the_past(match):
    """Running score in each goal fact must equal goals seen so far."""
    events, ctx = match
    det = MomentDetector(ctx)
    seen = {ctx.home: 0, ctx.away: 0}
    for e in events:
        if e.type == "goal":
            seen[e.team] += 1
        for f in det.feed(e):
            if f.kind == "goal":
                assert (f.figures["home_goals"], f.figures["away_goals"]) == (seen[ctx.home], seen[ctx.away])
            assert max(f.evidence) <= e.event_id


def test_every_line_has_evidence_that_exists(match):
    events, ctx = match
    ids = {e.event_id for e in events}
    for line in collect(events, ctx):
        assert line.evidence and set(line.evidence) <= ids


def test_model_line_blocked_falls_back_to_safe_line(match):
    """A model line with a made-up number is never shown; the template line is."""
    events, ctx = match
    first_goal = next(e for e in events if e.type == "goal")
    prefix = events[: first_goal.event_id]
    n_moments = len(collect(prefix, ctx))
    good = json.dumps({"text": "And we're under way!", "fact_ids": ["kickoff"]})
    replies = [good] + [json.dumps({"text": "Back and forth.", "fact_ids": ["kickoff"]})] * (n_moments - 2)
    replies.append(json.dumps({"text": "Holloway scores from 30 yards!", "fact_ids": [f"moment-{first_goal.event_id}"]}))
    lines = collect(prefix, ctx, LLMNarrator(ScriptedChatClient(replies)))
    assert lines[0].source == "model" and lines[0].text == "And we're under way!"
    goal_line = lines[-1]
    assert goal_line.kind == "goal" and goal_line.source == "template-fallback"
    assert any("'30'" in r for r in goal_line.blocked)
    assert "30 yards" not in goal_line.text and "Holloway" in goal_line.text
