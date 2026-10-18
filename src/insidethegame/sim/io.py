"""Read and write simulated matches."""

from __future__ import annotations

import json
from pathlib import Path

from .engine import GENERATOR_VERSION, MatchResult
from .schema import Event
from .stats import team_stats

DISCLAIMER = (
    "Synthetic data. All clubs, players and events are invented by a simulator "
    "and do not describe any real match."
)


def match_summary(result: MatchResult) -> dict:
    ids = (result.home.id, result.away.id)
    players = {p.id: p.name for t in (result.home, result.away) for p in t.players}
    goals = [
        {
            "event_id": e.event_id,
            "team": e.team,
            "scorer": players.get(e.player, e.player),
            "display_clock": e.display_clock,
            "xg": round(e.xg or 0, 3),
        }
        for e in result.events
        if e.type == "goal"
    ]
    return {
        "match_id": result.match_id,
        "seed": result.seed,
        "generator_version": GENERATOR_VERSION,
        "disclaimer": DISCLAIMER,
        "home": result.home.to_dict(),
        "away": result.away.to_dict(),
        "score": result.score,
        "goals": goals,
        "substitutions": result.lineup_changes,
        "stats": team_stats(result.events, ids),
        "event_count": len(result.events),
    }


def write_match(result: MatchResult, out_dir: str | Path) -> Path:
    """Write events.jsonl and match.json into out_dir/<match_id>/."""
    path = Path(out_dir) / result.match_id
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "events.jsonl", "w", encoding="utf-8") as f:
        for e in result.events:
            f.write(json.dumps(e.to_dict(), separators=(",", ":")) + "\n")
    with open(path / "match.json", "w", encoding="utf-8") as f:
        json.dump(match_summary(result), f, indent=2)
    return path


def load_events(path: str | Path) -> list[Event]:
    with open(path, encoding="utf-8") as f:
        return [Event(**json.loads(line)) for line in f if line.strip()]
