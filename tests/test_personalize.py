"""Personalization: casual limits, profile differences, favourite framing, player cards, schema, names."""

import json
import re
from pathlib import Path

import jsonschema
import pytest

from insidethegame.agents.checker import Claim, FactChecker
from insidethegame.agents.facts import Fact, load_match
from insidethegame.personalize.overlay import (JARGON, ViewerProfile, apply_favourite, casual_violations,
                                               verify_items)
from insidethegame.personalize.players import totals_upto
from insidethegame.sim.teams import TEAMS

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "data" / "samples" / "NBA-HCF-7"
SCHEMA = json.loads((ROOT / "docs" / "overlay.schema.json").read_text())


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """A fresh template web bundle for the sample match."""
    import asyncio
    from insidethegame.agents.bake import bake
    root = tmp_path_factory.mktemp("bake")
    asyncio.run(bake(SAMPLE, root / "baked", ["analyst", "casual"], ["en", "es"], web_out=root / "web"))
    return root / "web" / "NBA-HCF-7"


def load(bundle, name):
    return json.loads((bundle / name).read_text())


def facts_of(bundle) -> dict[str, Fact]:
    return {k: Fact(**v) for k, v in load(bundle, "facts.json").items()}


@pytest.fixture(scope="module")
def ctx():
    return load_match(SAMPLE)[1]


def all_item_files(bundle):
    return sorted(bundle.glob("overlay.*.json"))


def all_items(bundle):
    items = []
    for f in all_item_files(bundle):
        items += load(bundle, f.name)["items"]
    for f in sorted((bundle / "players").glob("*.json")):
        p = json.loads(f.read_text())
        items += p["items"]["analyst"] + p["items"]["casual"]
    return items


def test_viewer_profile_validates():
    assert ViewerProfile("casual", "NBA", None, "es").lang == "es"
    with pytest.raises(ValueError):
        ViewerProfile("expert")
    with pytest.raises(ValueError):
        ViewerProfile("casual", lang="ar")


def test_casual_lines_are_short_plain_and_verified(bundle, ctx):
    facts = facts_of(bundle)
    for f in ("overlay.casual.json", "overlay.casual.NBA.json", "overlay.casual.HCF.json"):
        items = load(bundle, f)["items"]
        assert items
        for it in items:
            assert len(it["text"].split()) <= 20, it["text"]
            assert not any(re.search(rf"(?<![a-z]){re.escape(j)}(?![a-z])", it["text"].lower()) for j in JARGON)
            assert casual_violations(it["text"]) == [], it["text"]
        assert verify_items(items, facts, ctx) == []


def test_casual_rule_catches_violations():
    assert casual_violations("Finn Holloway scored from a 0.01 xG chance at 14'.")  # jargon and two numbers
    assert casual_violations(" ".join(["word"] * 21))
    assert casual_violations("GOAL! Northbridge Athletic 1-0 Harbor City FC at 14'.") == []  # the score is free


def test_analyst_and_casual_differ_for_every_moment(bundle):
    a = {i["fact_ids"][0]: i["text"] for i in load(bundle, "overlay.analyst.json")["items"]}
    c = {i["fact_ids"][0]: i["text"] for i in load(bundle, "overlay.casual.json")["items"]}
    assert a.keys() == c.keys()
    same = [k for k in a if a[k] == c[k]]
    assert same == [], same


def test_favourite_reorders_and_boosts_without_new_numbers(bundle, ctx):
    facts = facts_of(bundle)
    base = load(bundle, "overlay.analyst.json")["items"]
    fav = load(bundle, "overlay.analyst.NBA.json")["items"]
    assert len(fav) == len(base)
    boosted = [i for i in fav if i["boost"]]
    assert boosted and all(i["text"].startswith("Your team: ") for i in boosted if i["kind"] != "kickoff")
    assert all(facts[i["fact_ids"][0]].teams[0] == "NBA" for i in boosted)
    by_id = {i["fact_ids"][0]: i for i in base}
    num = re.compile(r"\d+(?:\.\d+)?")
    for i in fav:
        assert sorted(num.findall(i["text"])) == sorted(num.findall(by_id[i["fact_ids"][0]]["text"]))
        if i["boost"]:
            assert i["show_for_s"] > by_id[i["fact_ids"][0]]["show_for_s"]
    # Same instant: favourite first.
    for a, b in zip(fav, fav[1:]):
        if (a["period"], a["clock"], a["at_event"]) == (b["period"], b["clock"], b["at_event"]):
            assert a["boost"] >= b["boost"]
    assert verify_items(fav, facts, ctx) == []


def test_apply_favourite_moves_our_moment_first(ctx):
    facts = {"a": Fact("a", "goal", "x", teams=["HCF"]), "b": Fact("b", "save", "y", teams=["NBA"])}
    mk = lambda fid, text: {"id": f"m:analyst:all:ticker:{fid}", "profile": "analyst", "period": 1, "clock": 10.0,
                            "at_event": 5, "slot": "ticker", "kind": "save", "text": text, "translations": {},
                            "fact_ids": [fid], "show_for_s": 4.0, "boost": False}
    out = apply_favourite([mk("a", "Their moment."), mk("b", "Our moment.")], facts, "NBA")
    assert [i["fact_ids"][0] for i in out] == ["b", "a"] and out[0]["text"] == "Your team: Our moment."


def test_player_card_figures_match_recomputation(bundle):
    events, _ = load_match(SAMPLE)
    for f in sorted((bundle / "players").glob("*.json")):
        p = json.loads(f.read_text())
        pid = p["player"]["id"]
        assert p["card"], pid
        for snap in p["card"][:: max(1, len(p["card"]) // 7)] + [p["card"][-1]]:
            t = totals_upto(events, pid, snap["at_event"]).figures()
            assert {k: snap[k] for k in t} == t, (pid, snap["at_event"])


def test_player_card_lines_pass_the_checker(bundle, ctx):
    facts = facts_of(bundle)
    checker = FactChecker(facts, ctx)
    for f in sorted((bundle / "players").glob("*.json")):
        p = json.loads(f.read_text())
        for it in p["items"]["analyst"] + p["items"]["casual"]:
            assert checker.check(Claim(it["text"], it["fact_ids"])).ok, it["text"]


def test_focus_players_are_top_six_plus_keepers(bundle):
    m = load(bundle, "manifest.json")
    roles = [p["role"] for p in m["focus_players"]]
    assert 6 <= len(roles) <= 8 and roles.count("GK") >= 2


def test_every_item_validates_against_the_schema(bundle):
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for f in all_item_files(bundle):
        validator.validate(load(bundle, f.name))
    item_schema = {**SCHEMA["$defs"]["OverlayItem"], "$defs": SCHEMA["$defs"]}
    for f in sorted((bundle / "players").glob("*.json")):
        for it in json.loads(f.read_text())["items"]["analyst"]:
            jsonschema.validate(it, item_schema)


def test_all_fact_ids_exist(bundle):
    facts = load(bundle, "facts.json")
    for it in all_items(bundle):
        assert it["fact_ids"] and all(fid in facts for fid in it["fact_ids"]), it["id"]


def test_bundle_names_only_from_sim_teams(bundle):
    allowed_players = {p.name for t in TEAMS.values() for p in t.players}
    allowed_clubs = {t.name for t in TEAMS.values()} | {t.short for t in TEAMS.values()}
    m = load(bundle, "manifest.json")
    for t in m["teams"]:
        assert t["name"] in allowed_clubs and t["short"] in allowed_clubs and t["id"] in TEAMS
        for p in t["lineup"]["starters"] + t["lineup"]["bench"]:
            assert p["name"] in allowed_players
    for p in m["focus_players"]:
        assert p["name"] in allowed_players and p["team"] in TEAMS
    tl = load(bundle, "timeline.json")["events"]
    assert {e["team"] for e in tl} <= set(TEAMS)


def test_spanish_marked_unavailable_without_a_model(bundle):
    m = load(bundle, "manifest.json")
    assert m["langs"] == {"en": "verified", "es": "unavailable"}
    items = load(bundle, "overlay.casual.json")["items"]
    assert all(not i["translations"]["es"]["ok"] for i in items)
