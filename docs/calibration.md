# Calibration

**Aggregate only, synthetic output.** Calibration nudges a few simulator constants so that generated matches have league-level totals like a real top division. Nothing else crosses over: clubs, players and events stay the invented ones in `sim/teams.py`. No real names, crests, colors or branding are used, and no third-party file is committed.

Calibration is opt-in. Without `--calibration`, the simulator behaves exactly as before (`data/samples/NBA-HCF-7` is byte-identical).

## Source and licence

- **Source:** [openfootball/england](https://github.com/openfootball/england), top-division result lists for the last three complete seasons (2023-24, 2024-25, 2025-26), 1,140 matches.
- **Licence:** CC0 1.0 Universal (public domain dedication).
- **What is used:** final scores only. From them, `tools/calibrate/targets.py` computes league means and standard deviations per match and the home, draw and away rates.
- **What is stored:** `data/calibration/league_targets.json` holds those aggregates and nothing else: no team names, fixtures, dates or players. The raw season files live in `data/raw/`, which is git-ignored. Download them by hand; the code never touches the network.

## Method

1. `python -m tools.calibrate.targets` reads `data/raw/*.txt` (or `*.csv` with `home, away, home_goals, away_goals`) and writes the targets.
2. `python -m tools.calibrate.fit` runs a deterministic coordinate search over three `SimConfig` levers: `shot_base` (chance volume), `finish_scale` (conversion) and `home_advantage` (extra shot and finishing rate for the home side). Each evaluation simulates the same 200 matches (seeds 1000 to 1199, all 12 pairings of the four clubs) and scores the weighted squared relative error on the targeted metrics.
3. The result goes to `data/calibration/simconfig_calibrated.json` with its config hash, and the table below is regenerated.

Use it:

```bash
itg-sim generate --home NBA --away HCF --seed 7 --calibration data/calibration/simconfig_calibrated.json
```

The match's `match.json` records `sim_config` and `sim_config_hash` whenever a non-default config is used.

## Limits

- The fit is in-sample: it is scored on the same 200 seeds the tests use. On other seeds, expect normal sampling noise (about ±0.06 goals per team and ±3 points on each result rate at 200 matches).
- Only result-level totals are calibrated. Shots, passes, corners and cards keep their designed ranges and are not matched to real data.
- The four clubs have fixed, contrasting styles, so match-to-match spread comes from those styles, not from a real league's spread of team quality.

## Target vs simulated

<!-- calibration-table:start -->
| Metric | Target (league) | Default sim | Calibrated sim | Tolerance | Within |
|---|---|---|---|---|---|
| goals_per_team_match | 1.494 (sd 1.241) | 1.240 | 1.492 (sd 1.292) | ±0.15 | yes |
| home_goals_per_match | 1.613 (sd 1.279) | 1.195 | 1.600 (sd 1.360) | ±10% | yes |
| away_goals_per_match | 1.375 (sd 1.190) | 1.285 | 1.385 (sd 1.211) | ±10% | yes |
| total_goals_per_match | 2.988 (sd 1.628) | 2.480 | 2.985 (sd 1.770) | reported only |  |
| home_win_rate | 0.432 (sd 0.495) | 0.370 | 0.430 (sd 0.495) | ±10% | yes |
| draw_rate | 0.245 (sd 0.430) | 0.255 | 0.240 (sd 0.427) | ±10% | yes |
| away_win_rate | 0.324 (sd 0.468) | 0.375 | 0.330 (sd 0.470) | ±10% | yes |

Fitted config (`96621e5d5264`): `shot_base=0.029`, `finish_scale=1.06`, `home_advantage=0.09`
<!-- calibration-table:end -->
