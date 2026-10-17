# Event schema

Every match is written as two files:

| File | What it holds |
|---|---|
| `events.jsonl` | One JSON event per line, in match order |
| `match.json` | Teams, lineups, score, goals, substitutions, team stats, seed, generator version |

All data is synthetic. Clubs, players and events are invented by the simulator.

## Coordinates

The pitch is 105 x 68 meters. Coordinates are in the **attacking frame of the team on the event**: that team always attacks toward `x = 105`, and the goal it attacks is centered at `(105, 34)`. To put both teams on one pitch, mirror the other team with `(105 - x, 68 - y)`.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `match_id` | string | `HOME-AWAY-SEED` |
| `event_id` | int | 1, 2, 3 ... in match order. Agents cite these. |
| `period` | 1 or 2 | Half |
| `clock` | float | Seconds since the start of the half |
| `minute` | int | Elapsed match minutes, 0-based (stoppage keeps counting: 47 in first-half stoppage) |
| `display_clock` | string | Broadcast style: `14'`, `45+2'`, `90+3'` |
| `team` | string | Acting team id |
| `player` | string or null | Acting player id, e.g. `NBA-9` |
| `type` | string | See event types below |
| `outcome` | string or null | Depends on type (below) |
| `x`, `y` | float | Where it happened |
| `end_x`, `end_y` | float or null | Where the ball went (passes, carries, corners, shots) |
| `possession_id` | int | Groups events in one spell of possession |
| `secondary_player` | string or null | Pass receiver, fouled player, or player coming on |
| `xg` | float or null | Expected goals, on shots and goals |
| `related_event` | int or null | Goal -> its shot, save -> the shot, card -> the foul |
| `tags` | list | See tags below |

## Event types and outcomes

| Type | Outcomes |
|---|---|
| `kickoff` | none |
| `pass` | `complete`, `incomplete` |
| `carry` | `complete`, `dispossessed` |
| `shot` | `goal`, `saved`, `blocked`, `off_target` |
| `goal` | none (links to its shot) |
| `save` | none (links to the shot) |
| `tackle`, `interception` | `won` |
| `foul` | none (`secondary_player` is the fouled player) |
| `card` | `yellow`, `red` |
| `free_kick` | `direct` (shot follows), `short` (play continues) |
| `corner` | none |
| `clearance` | none, or `out_for_corner` |
| `substitution` | none (`player` goes off, `secondary_player` comes on) |
| `half_end` | none |

## Tags

| Tag | Rule |
|---|---|
| `counter_attack` | Ball won inside 40 m of own goal, then reached the final 30 m within 12 s and 5 actions. Applied to the whole possession. |
| `high_press_win` | Tackle or interception within 35 m of the opponent's goal |
| `set_piece` | Corner or free kick, and the play that follows it |
| `header` | Headed shot |
| `big_chance` | Shot with xG of 0.30 or more |
| `second_yellow` | Red card from a second booking |

## xG model

Logistic on distance to goal center and the angle the goal mouth covers, minus a penalty for headers. Free-kick shots are scaled down by 25%. It is a simple illustrative model, not a trained one.

## Realism check

`tests/test_sim.py` simulates 120 matches and checks per-team averages against ranges set as targets for this generator (not taken from a cited source): goals 1.0 to 2.0, shots 10 to 18, pass accuracy 78% to 88%, corners 3 to 8, fouls 8 to 14, red cards under 0.2. It also checks that team styles show up: the possession side keeps more of the ball and the pressing side wins it back higher.
