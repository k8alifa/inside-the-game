"""Calibration: aggregate targets, fitted config, byte-identical default, name guard. No network."""

import filecmp
import json
import re
from pathlib import Path

import pytest

from insidethegame.sim import TEAMS, MatchSimulator, SimConfig
from insidethegame.sim.io import write_match
from tools.calibrate.fit import TARGETED, simulated_metrics, within_tolerance
from tools.calibrate.targets import build, compute_targets, read_results, team_names

ROOT = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "calib_mini.csv"
CAL = ROOT / "data" / "calibration"
RAW = ROOT / "data" / "raw"
CAL_SAMPLE = ROOT / "data" / "samples" / "calibrated-NBA-HCF-7"


def test_targets_end_to_end_on_fixture(tmp_path):
    (tmp_path / "mini.csv").write_text(FIXTURE.read_text())
    t = build(tmp_path)
    m = t["metrics"]
    assert t["n_matches"] == 8 and t["seasons"] == ["mini-1", "mini-2"]
    assert m["home_goals_per_match"]["mean"] == pytest.approx(13 / 8)
    assert m["away_goals_per_match"]["mean"] == pytest.approx(10 / 8)
    assert m["goals_per_team_match"]["mean"] == pytest.approx(23 / 16)
    assert (m["home_win_rate"]["mean"], m["draw_rate"]["mean"], m["away_win_rate"]["mean"]) == (0.375, 0.375, 0.25)
    blob = json.dumps(t)
    for name in team_names(read_results(tmp_path)):
        assert name not in blob  # aggregates only


def test_targets_fail_clearly_without_raw_files(tmp_path):
    with pytest.raises(SystemExit, match="No raw result files"):
        build(tmp_path)


def test_default_config_reproduces_sample_byte_for_byte(tmp_path):
    assert SimConfig().is_default()
    out = write_match(MatchSimulator(TEAMS["NBA"], TEAMS["HCF"], 7, config=SimConfig()).run(), tmp_path)
    for f in ("events.jsonl", "match.json"):
        assert filecmp.cmp(out / f, ROOT / "data" / "samples" / "NBA-HCF-7" / f, shallow=False), f


def test_calibrated_config_hits_targets():
    targets = json.loads((CAL / "league_targets.json").read_text())["metrics"]
    cfg = SimConfig.load(CAL / "simconfig_calibrated.json")
    assert not cfg.is_default()
    sim = simulated_metrics(cfg)  # 200 matches, the fit's fixed seeds
    assert abs(sim["goals_per_team_match"]["mean"] - targets["goals_per_team_match"]["mean"]) <= 0.15
    ok = within_tolerance(sim, targets)
    assert all(ok.values()), {k: (sim[k]["mean"], targets[k]["mean"]) for k in TARGETED if not ok[k]}


def test_calibrated_sample_matches_its_config():
    m = json.loads((CAL_SAMPLE / "match.json").read_text())
    cfg = SimConfig.load(CAL / "simconfig_calibrated.json")
    assert m["sim_config_hash"] == cfg.config_hash() and m["sim_config"] == cfg.to_dict()
    assert json.loads((CAL / "simconfig_calibrated.json").read_text())["config_hash"] == cfg.config_hash()


def _files_to_guard() -> list[Path]:
    return sorted(CAL_SAMPLE.glob("*")) + sorted(CAL.glob("*.json"))


def test_only_fictional_team_ids_in_calibrated_outputs():
    for line in (CAL_SAMPLE / "events.jsonl").read_text().splitlines():
        assert json.loads(line)["team"] in TEAMS
    m = json.loads((CAL_SAMPLE / "match.json").read_text())
    assert {m["home"]["id"], m["away"]["id"]} <= set(TEAMS)
    targets = json.loads((CAL / "league_targets.json").read_text())
    assert set(targets) == {"metrics", "n_matches", "seasons", "source", "licence", "note", "generated_at"}


@pytest.mark.skipif(not any(RAW.glob("*.txt")) and not any(RAW.glob("*.csv")), reason="no raw files present")
def test_no_raw_team_names_leak_into_committed_outputs():
    names = set()
    for n in team_names(read_results(RAW)):
        names.add(n)
        names.add(re.sub(r"^(AFC|FC)\s+|\s+(AFC|FC)$", "", n))
    text = "\n".join(p.read_text(encoding="utf-8") for p in _files_to_guard())
    leaked = sorted(n for n in names if re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE))
    assert leaked == []
