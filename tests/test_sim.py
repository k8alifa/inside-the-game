import itertools
import statistics

import pytest

from insidethegame.sim import TEAMS, MatchSimulator, load_events, team_stats, write_match, xg_model
from insidethegame.sim.schema import EVENT_TYPES, PITCH_LENGTH, PITCH_WIDTH

PAIRS = list(itertools.permutations(TEAMS, 2))


def sim(seed, pair=None):
    h, a = pair or PAIRS[seed % len(PAIRS)]
    return MatchSimulator(TEAMS[h], TEAMS[a], seed).run()


@pytest.fixture(scope="module")
def many():
    return [sim(s) for s in range(120)]


def test_same_seed_same_match():
    a, b = sim(42), sim(42)
    assert [e.to_dict() for e in a.events] == [e.to_dict() for e in b.events]


def test_different_seed_different_match():
    assert [e.to_dict() for e in sim(1, PAIRS[0]).events] != [e.to_dict() for e in sim(2, PAIRS[0]).events]


def test_event_integrity(many):
    for r in many:
        ids = [e.event_id for e in r.events]
        assert ids == list(range(1, len(ids) + 1))
        assert sum(e.type == "half_end" for e in r.events) == 2
        teams = {r.home.id, r.away.id}
        for prev, e in zip(r.events, r.events[1:]):
            if e.period == prev.period:
                assert e.clock >= prev.clock
        for e in r.events:
            assert e.type in EVENT_TYPES
            assert e.team in teams
            for v, hi in ((e.x, PITCH_LENGTH), (e.y, PITCH_WIDTH), (e.end_x, PITCH_LENGTH), (e.end_y, PITCH_WIDTH)):
                assert v is None or 0 <= v <= hi


def test_goals_link_to_shots_and_score(many):
    for r in many:
        by_id = {e.event_id: e for e in r.events}
        for g in (e for e in r.events if e.type == "goal"):
            shot = by_id[g.related_event]
            assert shot.type == "shot" and shot.outcome == "goal" and shot.team == g.team
        stats = team_stats(r.events, (r.home.id, r.away.id))
        assert {t: s["goals"] for t, s in stats.items()} == r.score


def test_players_on_pitch_only(many):
    """No event by a player before coming on, after going off, or after a red card."""
    for r in many:
        gone, unused = set(), {p.id for t in (r.home, r.away) for p in t.bench}
        for e in r.events:
            if e.player:
                assert e.player not in gone, (r.match_id, e.event_id)
                assert e.player not in unused, (r.match_id, e.event_id)
            if e.type == "substitution":
                gone.add(e.player)
                unused.discard(e.secondary_player)
            if e.type == "card" and e.outcome == "red":
                gone.add(e.player)


def test_aggregate_realism(many):
    """Averages per team per match sit in plausible top-flight ranges."""
    rows = [s for r in many for s in team_stats(r.events, (r.home.id, r.away.id)).values()]
    mean = lambda k: statistics.mean(x[k] for x in rows)
    assert 1.0 <= mean("goals") <= 2.0
    assert 10 <= mean("shots") <= 18
    assert 1.0 <= mean("xg") <= 2.0
    assert 78 <= mean("pass_accuracy_pct") <= 88
    assert 3 <= mean("corners") <= 8
    assert 8 <= mean("fouls") <= 14
    assert mean("red_cards") < 0.2
    assert any("counter_attack" in e.tags for r in many for e in r.events)


def test_styles_show_in_stats(many):
    """The possession team keeps the ball more; the pressing team wins it higher."""
    agg = {t: [] for t in TEAMS}
    for r in many:
        for t, s in team_stats(r.events, (r.home.id, r.away.id)).items():
            agg[t].append(s)
    mean = lambda t, k: statistics.mean(x[k] for x in agg[t])
    assert mean("NBA", "possession_pct") > mean("HCF", "possession_pct") + 8
    assert mean("NBA", "high_press_wins") > mean("HCF", "high_press_wins")


def test_xg_model_shape():
    assert xg_model(100, 34) > xg_model(90, 34) > xg_model(80, 34)
    assert xg_model(94, 34) > xg_model(94, 5)
    assert xg_model(94, 34, header=True) < xg_model(94, 34)


def test_round_trip(tmp_path):
    r = sim(3)
    path = write_match(r, tmp_path)
    back = load_events(path / "events.jsonl")
    assert len(back) == len(r.events)
    assert [e.type for e in back] == [e.type for e in r.events]
    assert (path / "match.json").exists()
