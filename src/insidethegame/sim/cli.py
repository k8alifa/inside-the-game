"""Command line: generate matches or replay one as a live feed.

    python -m insidethegame.sim generate --home NBA --away HCF --seed 7
    python -m insidethegame.sim replay data/matches/NBA-HCF-7/events.jsonl --speed 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .config import SimConfig
from .engine import MatchSimulator
from .io import load_events, write_match
from .teams import TEAMS


def _generate(args: argparse.Namespace) -> int:
    for t in (args.home, args.away):
        if t not in TEAMS:
            print(f"Unknown team {t!r}. Choose from: {', '.join(TEAMS)}", file=sys.stderr)
            return 2
    config = SimConfig.load(args.calibration) if args.calibration else None
    if config is not None:
        print(f"calibration: {args.calibration} (config {config.config_hash()})")
    for i in range(args.count):
        seed = args.seed + i
        result = MatchSimulator(TEAMS[args.home], TEAMS[args.away], seed, config=config).run()
        path = write_match(result, args.out)
        score = result.score
        print(f"{result.match_id}: {args.home} {score[args.home]}-{score[args.away]} {args.away} "
              f"({len(result.events)} events) -> {path}")
    return 0


def _replay(args: argparse.Namespace) -> int:
    """Emit events as JSON lines, paced by match clock / speed.

    --speed 60 plays 90 minutes in about 90 seconds. --speed 0 emits instantly.
    """
    events = load_events(args.events)
    prev_period, prev_clock = None, 0.0
    try:
        for e in events:
            if args.speed > 0 and e.period == prev_period:
                time.sleep(max(0.0, e.clock - prev_clock) / args.speed)
            prev_period, prev_clock = e.period, e.clock
            sys.stdout.write(json.dumps(e.to_dict(), separators=(",", ":")) + "\n")
            sys.stdout.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itg-sim", description="Synthetic football match generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="simulate matches and write them to disk")
    g.add_argument("--home", default="NBA")
    g.add_argument("--away", default="HCF")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--count", type=int, default=1, help="number of matches (seeds seed..seed+count-1)")
    g.add_argument("--out", default="data/matches")
    g.add_argument("--calibration", default=None,
                   help="SimConfig JSON from tools/calibrate (default: none, the original engine constants)")
    g.set_defaults(func=_generate)

    r = sub.add_parser("replay", help="stream a match's events as a live feed")
    r.add_argument("events", help="path to events.jsonl")
    r.add_argument("--speed", type=float, default=60.0)
    r.set_defaults(func=_replay)

    sub.add_parser("teams", help="list available fictional teams").set_defaults(
        func=lambda a: print("\n".join(f"{t.id}  {t.name}" for t in TEAMS.values())) or 0
    )

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
