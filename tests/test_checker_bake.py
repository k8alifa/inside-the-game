"""Checker fixes, counterattack tag timing, and the itg-bake output schema."""

import asyncio
import itertools
import json
import re
from pathlib import Path

import pytest
from agent_framework import BaseChatClient, ChatResponse, Message

from insidethegame.agents.bake import SCHEMA_VERSION, bake
from insidethegame.agents.checker import Claim, FactChecker
from insidethegame.agents.facts import build_facts, load_match
from insidethegame.agents.names import LEAGUE
from insidethegame.agents.translator import check_translation
from insidethegame.sim import TEAMS, MatchSimulator

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "NBA-HCF-7"
OTHER = TEAMS["ELR"].players[3]  # a player from a club not in the sample match


@pytest.fixture(scope="module")
def match():
    return load_match(SAMPLE)


@pytest.fixture(scope="module")
def checker(match):
    events, ctx = match
    return FactChecker(build_facts(events, ctx), ctx)


# ------------------------------------------------------------ checker
@pytest.mark.parametrize("text, ids, reason", [
    ("Elmstead were watching from the stands.", ["final-score"], "team 'Elmstead'"),
    ("kingsford would have enjoyed that.", ["final-score"], "team 'Kingsford'"),
    ("Finn opened the scoring.", ["goal-2"], "player 'Finn' (first name)"),
    ("Underhill opened the scoring.", ["goal-1"], "player 'Underhill' (surname)"),
    (f"{OTHER.name} watched on.", ["goal-1"], "not in this match"),
    (f"{OTHER.name.split()[-1]} watched on.", ["goal-1"], "not in this match"),
    ("Northbridge pressed high and won one ball that mattered.", ["press-NBA"], "number 'one'"),
    ("Northbridge struck eleven times.", ["final-score"], "number 'eleven'"),
])
def test_checker_blocks_new_cases(checker, text, ids, reason):
    v = checker.check(Claim(text, ids))
    assert not v.ok and any(reason in r for r in v.reasons), v.reasons


@pytest.mark.parametrize("text, ids", [
    ("Northbridge beat Harbor City 3-1.", ["final-score"]),
    ("Finn opened the scoring.", ["goal-1"]),
    ("Holloway opened the scoring.", ["goal-1"]),
    ("One of the goals of the season: Holloway from 0.01 xG.", ["goal-1"]),
    ("Northbridge scored three.", ["final-score"]),
])
def test_checker_allows_supported_short_forms(checker, text, ids):
    v = checker.check(Claim(text, ids))
    assert v.ok, v.reasons


def test_league_surnames_are_unique():
    lasts = [p.last for p in LEAGUE.players]
    assert len(lasts) == len(set(lasts))


SRC = "Kai Underhill scored for Northbridge Athletic at 90+2', set up by Leon Okonjo."


@pytest.mark.parametrize("lang, text, reason", [
    ("es", "Kai Underhill marcó para Northbridge Athletic en el 90+2', asistido por Leon Okonjo.", None),
    ("es", "Kai Underhill marcó para Northbridge Athletic en el 90+2', asistido por Leon Holloway.", "'Holloway' added"),
    ("es", "Kai marcó para Northbridge Athletic en el 90+2', asistido por Leon Okonjo.", "'Underhill' missing"),
    ("es", "Kai Underhill marcó para Northbridge Athletic en el 90+3', asistido por Leon Okonjo.", "numbers changed"),
    ("ar", "anything", "unsupported language 'ar'"),
])
def test_translation_checks(match, lang, text, reason):
    _, ctx = match
    reasons = check_translation(SRC, text, lang, ctx)
    if reason is None:
        assert reasons == []
    else:
        assert any(reason in r for r in reasons), reasons


def test_only_english_and_spanish_are_accepted():
    from insidethegame.agents.translator import parse_langs
    assert parse_langs("en,es") == ["es"] and parse_langs("") == []
    for bad in ("ar", "en,ja", "hi", "fr"):
        with pytest.raises(ValueError, match="supported: en, es"):
            parse_langs(bad)


# --------------------------------------------------- tag timing (sim)
def test_counter_tag_never_precedes_the_counter():
    pairs = list(itertools.permutations(TEAMS, 2))
    checked = 0
    for seed in range(40):
        h, a = pairs[seed % len(pairs)]
        events = MatchSimulator(TEAMS[h], TEAMS[a], seed).run().events
        by_poss = {}
        for e in events:
            by_poss.setdefault(e.possession_id, []).append(e)
        for evs in by_poss.values():
            if not any("counter_attack" in e.tags for e in evs):
                continue
            team = next(e.team for e in evs if "counter_attack" in e.tags)
            evs = [e for e in evs if e.team == team and e.type not in ("half_end", "substitution")]  # on-ball actions
            tagged = [i for i, e in enumerate(evs) if "counter_attack" in e.tags]
            checked += 1
            first = tagged[0]
            assert tagged == list(range(first, len(evs)))  # a suffix of the possession
            trigger = evs[first]
            assert trigger.type in ("pass", "carry") and trigger.outcome == "complete"
            assert trigger.end_x >= 75 and first > 0
    assert checked > 20


# ------------------------------------------------------------ bake
REQUIRED_LINE = {"display_clock", "text", "fact_ids", "evidence", "source", "kind", "blocked",
                 "at_event", "period", "clock", "profile", "translations"}
REQUIRED_PARA = {"text", "fact_ids", "kind", "order", "source", "verified", "evidence", "translations"}


def validate_bundle(out: Path, profiles, langs):
    m = json.loads((out / "manifest.json").read_text())
    assert m["schema_version"] == SCHEMA_VERSION and m["match_id"] == "NBA-HCF-7"
    assert m["profiles"] == profiles and m["langs"]["en"] == "verified"
    assert set(m["langs"]) == set(langs) and set(m["counts"]["langs"]) == set(langs)
    facts = json.loads((out / "facts.json").read_text())
    for p in profiles:
        n = json.loads((out / f"narration.{p}.json").read_text())
        assert n["profile"] == p and n["lines"]
        for line in n["lines"]:
            assert REQUIRED_LINE <= line.keys()
            assert line["source"] in ("model", "template", "template-fallback")
            assert set(line["translations"]) == set(langs) - {"en"}
            assert line["evidence"]
        clocks = [(l["period"], l["clock"]) for l in n["lines"]]
        assert clocks == sorted(clocks)
        r = json.loads((out / f"recap.{p}.json").read_text())
        assert r["headline"]["kind"] == "headline"
        for para in r["paragraphs"]:
            assert REQUIRED_PARA <= para.keys() and para["verified"]
            assert all(f in facts for f in para["fact_ids"])
    return m


def test_bake_template_schema(tmp_path):
    m = asyncio.run(bake(SAMPLE, tmp_path, ["analyst", "casual"], ["en", "es"]))
    validate_bundle(tmp_path, ["analyst", "casual"], ["en", "es"])
    assert m["provider"] == "template" and set(m["counts"]["sources"]) == {"template"}
    assert m["langs"]["es"] == "unavailable" and set(m["counts"]["langs"]["es"]) == {"unavailable"}
    assert m["generated_at"] is None  # deterministic template bake


class FakeModel(BaseChatClient):
    """Answers by prompt type. One narration line per batch carries a made-up number."""

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        prompt = messages[-1].text
        if "Moments, in match order" in prompt:
            ids = re.findall(r"^- (\S+) \[", prompt, re.M)
            lines = [{"moment": i, "text": "Big moment here.", "fact_ids": [i]} for i in ids]
            lines[0]["text"] = "That was his 99th goal."  # must be blocked
            reply = {"lines": lines}
        elif prompt.startswith("Languages:"):
            texts = re.findall(r"^(\d+)\. (.*)$", prompt.split("Texts:")[1], re.M)
            reply = {"translations": [{"index": int(i), "es": t} for i, t in texts]}
        else:  # recap writer
            reply = {"claims": [
                {"text": "Northbridge beat Harbor City", "fact_ids": ["final-score"], "kind": "headline", "order": 0},
                {"text": "Northbridge won 3-1.", "fact_ids": ["final-score"], "kind": "paragraph", "order": 1},
            ]}
        return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(reply, ensure_ascii=False)])])


def test_bake_with_model_schema_and_counts(tmp_path):
    m = asyncio.run(bake(SAMPLE, tmp_path, ["analyst"], ["en", "es"], client=FakeModel()))
    validate_bundle(tmp_path, ["analyst"], ["en", "es"])
    n = json.loads((tmp_path / "narration.analyst.json").read_text())["lines"]
    batches = (len(n) + 11) // 12
    assert sum(l["source"] == "template-fallback" for l in n) == batches  # the 99th-goal lines
    assert sum(l["source"] == "model" for l in n) == len(n) - batches
    assert all("number '99'" in l["blocked"][0] for l in n if l["source"] == "template-fallback")
    assert m["counts"]["langs"]["es"].get("ok", 0) > 0 and m["langs"]["es"] in ("verified", "partial")
    first_model = next(l for l in n if l["source"] == "model")
    assert first_model["translations"]["es"]["ok"] and set(first_model["translations"]) == {"es"}


# ---------------------------------------------------- result direction
@pytest.mark.parametrize("text, ids", [
    ("Harbor City FC won 3-1.", ["final-score"]),
    ("Harbor City beat Northbridge.", ["final-score"]),
    ("Northbridge lost 3-1 at home.", ["final-score"]),
    ("Northbridge were beaten.", ["final-score"]),
    ("Northbridge and Harbor City drew.", ["final-score"]),
    ("A win for Harbor City.", ["final-score"]),
    ("Harbor City lead 1-0.", ["goal-1"]),       # current score from the cited goal fact
    ("Harbor City are winning.", ["goal-1"]),
    ("Harbor City drew level.", ["goal-3"]),     # it was 2-1 after that goal
])
def test_result_direction_blocks_contradictions(checker, text, ids):
    v = checker.check(Claim(text, ids))
    assert not v.ok and any("result direction" in r for r in v.reasons), v.reasons


@pytest.mark.parametrize("text, ids", [
    ("Northbridge won 3-1.", ["final-score"]),
    ("Northbridge beat Harbor City 3-1.", ["final-score"]),
    ("Harbor City were beaten.", ["final-score"]),
    ("Harbor City fell to Northbridge.", ["final-score"]),
    ("Northbridge's win was deserved.", ["final-score"]),
    ("Northbridge lead 1-0.", ["goal-1"]),
    ("Harbor City won the ball back high.", ["final-score"]),   # not a result
    ("Harbor City won a corner.", ["final-score"]),
    ("Northbridge lost possession.", ["final-score"]),
])
def test_result_direction_allows_true_or_unrelated_claims(checker, text, ids):
    v = checker.check(Claim(text, ids))
    assert not any("result direction" in r for r in v.reasons), v.reasons


def test_result_direction_uses_current_score_live(match):
    """Without a cited score, a live checker uses the latest score it has seen."""
    from insidethegame.agents.narrator import MomentDetector
    events, ctx = match
    det = MomentDetector(ctx)
    for e in events[:300]:  # just after the first goal (1-0 home)
        det.feed(e)
    live = FactChecker(det.facts, ctx)
    assert not live.check(Claim("Harbor City are ahead.", ["kickoff"])).ok
    assert not any("result direction" in r for r in live.check(Claim("Northbridge are ahead.", ["kickoff"])).reasons)
