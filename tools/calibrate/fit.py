"""Fit SimConfig to league targets with a deterministic coordinate search.

    python -m tools.calibrate.fit --targets data/calibration/league_targets.json

Each evaluation simulates the same fixed set of matches (fixed seeds, all 12
pairings of the four fictional clubs) and compares league aggregates with the
targets. The search changes only the levers that drive totals and home
advantage. Output:

  data/calibration/simconfig_calibrated.json   the fitted SimConfig
  docs/calibration.md                         target vs simulated table

Clubs and players stay the invented ones in sim/teams.py.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from insidethegame.sim import TEAMS, MatchSimulator
from insidethegame.sim.config import SimConfig

from .targets import Result, compute_targets

N_MATCHES = 200
SEED0 = 1000
# metric -> (weight, tolerance used in docs/tests: "abs" absolute or "rel" relative)
TARGETED = {
    "goals_per_team_match": (3.0, ("abs", 0.15)),
    "home_goals_per_match": (1.0, ("rel", 0.10)),
    "away_goals_per_match": (1.0, ("rel", 0.10)),
    "home_win_rate": (1.0, ("rel", 0.10)),
    "draw_rate": (1.0, ("rel", 0.10)),
    "away_win_rate": (1.0, ("rel", 0.10)),
}
SEARCH = {  # parameter -> (lower, upper, first step)
    "shot_base": (0.010, 0.060, 0.006),
    "finish_scale": (0.60, 1.60, 0.12),
    "home_advantage": (0.0, 0.50, 0.08),
}


def simulate_results(cfg: SimConfig, n: int = N_MATCHES, seed0: int = SEED0) -> list[Result]:
    pairs = list(itertools.permutations(TEAMS, 2))
    out = []
    for i in range(n):
        h, a = pairs[i % len(pairs)]
        r = MatchSimulator(TEAMS[h], TEAMS[a], seed0 + i, config=cfg).run()
        s = r.score
        out.append(Result("sim", h, a, s[h], s[a]))
    return out


def simulated_metrics(cfg: SimConfig, n: int = N_MATCHES, seed0: int = SEED0) -> dict:
    return compute_targets(simulate_results(cfg, n, seed0))


def loss(sim: dict, targets: dict) -> float:
    total = 0.0
    for metric, (weight, _) in TARGETED.items():
        t, s = targets[metric]["mean"], sim[metric]["mean"]
        total += weight * ((s - t) / t) ** 2
    return total


def within_tolerance(sim: dict, targets: dict) -> dict[str, bool]:
    out = {}
    for metric, (_, (kind, tol)) in TARGETED.items():
        t, s = targets[metric]["mean"], sim[metric]["mean"]
        out[metric] = abs(s - t) <= tol if kind == "abs" else abs(s - t) <= tol * t
    return out


def fit(targets: dict, start: SimConfig | None = None, rounds: int = 8, log=print) -> tuple[SimConfig, dict, int]:
    cfg = start or SimConfig()
    cache: dict[SimConfig, tuple[float, dict]] = {}

    def evaluate(c: SimConfig) -> tuple[float, dict]:
        if c not in cache:
            sim = simulated_metrics(c)
            cache[c] = (loss(sim, targets), sim)
        return cache[c]

    best_loss, best_sim = evaluate(cfg)
    steps = {k: v[2] for k, v in SEARCH.items()}
    for r in range(rounds):
        improved = False
        for name, (lo, hi, _) in SEARCH.items():
            for direction in (+1, -1):
                while True:
                    value = round(min(hi, max(lo, getattr(cfg, name) + direction * steps[name])), 5)
                    if value == getattr(cfg, name):
                        break
                    cand = replace(cfg, **{name: value})
                    cand_loss, cand_sim = evaluate(cand)
                    if cand_loss < best_loss:
                        cfg, best_loss, best_sim, improved = cand, cand_loss, cand_sim, True
                    else:
                        break
        log(f"round {r + 1}: loss {best_loss:.5f}  " + ", ".join(f"{k}={getattr(cfg, k)}" for k in SEARCH))
        if not improved:
            steps = {k: v / 2 for k, v in steps.items()}
    return cfg, best_sim, len(cache)


def render_table(targets: dict, default_sim: dict, sim: dict) -> str:
    ok = within_tolerance(sim, targets["metrics"])
    rows = ["| Metric | Target (league) | Default sim | Calibrated sim | Tolerance | Within |",
            "|---|---|---|---|---|---|"]
    for metric, v in targets["metrics"].items():
        tol = TARGETED.get(metric, (None, None))[1]
        tol_s = "reported only" if tol is None else (f"±{tol[1]:g}" if tol[0] == "abs" else f"±{tol[1]:.0%}")
        mark = "" if tol is None else ("yes" if ok[metric] else "no")
        rows.append(f"| {metric} | {v['mean']:.3f} (sd {v['sd']:.3f}) | {default_sim[metric]['mean']:.3f} "
                    f"| {sim[metric]['mean']:.3f} (sd {sim[metric]['sd']:.3f}) | {tol_s} | {mark} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fit SimConfig to league targets")
    p.add_argument("--targets", default="data/calibration/league_targets.json")
    p.add_argument("--out", default="data/calibration/simconfig_calibrated.json")
    p.add_argument("--doc", default="docs/calibration.md")
    args = p.parse_args(argv)

    targets_file = json.loads(Path(args.targets).read_text())
    targets = targets_file["metrics"]
    default_sim = simulated_metrics(SimConfig())
    cfg, sim, evals = fit(targets)

    out = {
        "config": cfg.to_dict(),
        "config_hash": cfg.config_hash(),
        "fit": {"method": "coordinate descent", "matches_per_evaluation": N_MATCHES, "seeds": [SEED0, SEED0 + N_MATCHES - 1],
                "evaluations": evals, "loss": round(loss(sim, targets), 6),
                "within_tolerance": within_tolerance(sim, targets)},
        "simulated": sim,
        "targets_from": {k: targets_file[k] for k in ("source", "licence", "seasons", "n_matches")},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")

    doc = Path(args.doc)
    table = render_table(targets_file, default_sim, sim)
    text = doc.read_text() if doc.exists() else ""
    start, end = "<!-- calibration-table:start -->", "<!-- calibration-table:end -->"
    block = f"{start}\n{table}\n\nFitted config (`{cfg.config_hash()}`): " + ", ".join(
        f"`{k}={getattr(cfg, k)}`" for k in SEARCH) + f"\n{end}"
    if start in text and end in text:
        text = text[: text.index(start)] + block + text[text.index(end) + len(end):]
    else:
        text = (text.rstrip() + "\n\n" if text else "") + block + "\n"
    doc.write_text(text)
    print(table)
    print(f"\nwrote {args.out} and the table in {args.doc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
