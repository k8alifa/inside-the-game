"""Build league-level calibration targets from local result files.

    python -m tools.calibrate.targets --raw data/raw --out data/calibration/league_targets.json

Input: files placed by hand in data/raw/ (never committed):
  - *.txt  openfootball Football.TXT result lists (one season per file,
           season taken from the file name, e.g. 2024-25.txt)
  - *.csv  columns home, away, home_goals, away_goals, optional season

Output: aggregates only. League mean and SD per match, plus home, draw and
away rates. No team names, no fixtures, no player data.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "openfootball/england (top division result lists)"
LICENCE = "CC0 1.0 Universal (public domain dedication). Only aggregates are stored here."

# "  19:00   Home Team  4-2 (1-0)  Away Team" (time and half-time score optional)
_LINE = re.compile(r"^\s*(?:\d{1,2}[.:]\d{2}\s+)?(?P<home>\S.*?)\s+(?P<hg>\d{1,2})-(?P<ag>\d{1,2})"
                   r"(?:\s*\(\d{1,2}-\d{1,2}\))?\s+(?P<away>\S.*?)\s*$")
# "  20:00  Home Team    v Away Team    1-0 (0-0)" (older layout)
_LINE_V = re.compile(r"^\s*(?:\d{1,2}[.:]\d{2}\s+)?(?P<home>\S.*?)\s+v\s+(?P<away>\S.*?)\s+"
                     r"(?P<hg>\d{1,2})-(?P<ag>\d{1,2})(?:\s*\(\d{1,2}-\d{1,2}\))?\s*$")


@dataclass(frozen=True)
class Result:
    season: str
    home: str
    away: str
    home_goals: int
    away_goals: int


def parse_txt(path: Path) -> list[Result]:
    season = path.stem
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "(#=▪[" or stripped.startswith(("Matchday", "Round")):
            continue
        m = _LINE_V.match(line) or _LINE.match(line)
        if m and not re.search(r"\d", m.group("home")) and not re.search(r"\d", m.group("away")):
            out.append(Result(season, m.group("home"), m.group("away"), int(m.group("hg")), int(m.group("ag"))))
    return out


def parse_csv(path: Path) -> list[Result]:
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(Result(row.get("season") or path.stem, row["home"].strip(), row["away"].strip(),
                              int(row["home_goals"]), int(row["away_goals"])))
    return out


def read_results(raw_dir: str | Path) -> list[Result]:
    raw = Path(raw_dir)
    files = sorted(list(raw.glob("*.txt")) + list(raw.glob("*.csv"))) if raw.is_dir() else []
    files = [f for f in files if f.name.upper() not in ("LICENSE.TXT", "README.TXT")]
    if not files:
        raise SystemExit(
            f"No raw result files in {raw}/. Put season files there by hand (see docs/calibration.md); "
            "they are git-ignored and never committed.")
    results = []
    for f in files:
        results += parse_csv(f) if f.suffix == ".csv" else parse_txt(f)
    if not results:
        raise SystemExit(f"Found files in {raw}/ but no results could be parsed.")
    return results


def team_names(results: list[Result]) -> set[str]:
    return {r.home for r in results} | {r.away for r in results}


def _ms(values: list[float]) -> dict:
    return {"mean": round(statistics.fmean(values), 4), "sd": round(statistics.pstdev(values), 4)}


def _rate(flags: list[bool]) -> dict:
    p = sum(flags) / len(flags)
    return {"mean": round(p, 4), "sd": round(math.sqrt(p * (1 - p)), 4)}


def compute_targets(results: list[Result]) -> dict:
    """Aggregate metrics. Per-team values pool the home and away side of every match."""
    team_goals = [r.home_goals for r in results] + [r.away_goals for r in results]
    return {
        "goals_per_team_match": _ms(team_goals),
        "home_goals_per_match": _ms([r.home_goals for r in results]),
        "away_goals_per_match": _ms([r.away_goals for r in results]),
        "total_goals_per_match": _ms([r.home_goals + r.away_goals for r in results]),
        "home_win_rate": _rate([r.home_goals > r.away_goals for r in results]),
        "draw_rate": _rate([r.home_goals == r.away_goals for r in results]),
        "away_win_rate": _rate([r.home_goals < r.away_goals for r in results]),
    }


def build(raw_dir: str | Path) -> dict:
    results = read_results(raw_dir)
    seasons = sorted({r.season for r in results})
    return {
        "metrics": compute_targets(results),
        "n_matches": len(results),
        "seasons": seasons,
        "source": SOURCE,
        "licence": LICENCE,
        "note": "Aggregate only. No team, player or fixture data. Generated output stays fully synthetic.",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="League-level calibration targets from local result files")
    p.add_argument("--raw", default="data/raw")
    p.add_argument("--out", default="data/calibration/league_targets.json")
    args = p.parse_args(argv)
    targets = build(args.raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(targets, indent=2) + "\n")
    print(f"{targets['n_matches']} matches, seasons {', '.join(targets['seasons'])} -> {out}")
    for k, v in targets["metrics"].items():
        print(f"  {k:24s} mean {v['mean']:.3f}  sd {v['sd']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
