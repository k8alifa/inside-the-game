"""Match statistics computed from the event stream.

These are the ground-truth numbers that agents must cite. Every figure here
is derived only from events, so any claim an agent makes can be traced back
to event ids.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .schema import Event


def team_stats(events: Iterable[Event], team_ids: tuple[str, str]) -> dict[str, dict]:
    s: dict[str, dict] = {t: defaultdict(float) for t in team_ids}
    for e in events:
        if e.team not in s:
            continue
        t = s[e.team]
        if e.type == "pass":
            t["passes"] += 1
            t["passes_completed"] += e.outcome == "complete"
        elif e.type == "shot":
            t["shots"] += 1
            t["shots_on_target"] += e.outcome in ("goal", "saved")
            t["xg"] += e.xg or 0.0
            t["big_chances"] += "big_chance" in e.tags
        elif e.type == "goal":
            t["goals"] += 1
        elif e.type == "corner":
            t["corners"] += 1
        elif e.type == "foul":
            t["fouls"] += 1
        elif e.type == "card":
            t["yellow_cards" if e.outcome == "yellow" else "red_cards"] += 1
        elif e.type in ("tackle", "interception"):
            t["ball_wins"] += 1
            t["high_press_wins"] += "high_press_win" in e.tags
    poss_events = {t: 0 for t in team_ids}
    for e in events:
        if e.team in poss_events and e.type in ("pass", "carry"):
            poss_events[e.team] += 1
    total = sum(poss_events.values()) or 1
    counters = {t: set() for t in team_ids}
    for e in events:
        if e.team in counters and "counter_attack" in e.tags:
            counters[e.team].add(e.possession_id)
    out = {}
    for tid in team_ids:
        t = s[tid]
        passes = t["passes"] or 1
        out[tid] = {
            "goals": int(t["goals"]),
            "possession_pct": round(100 * poss_events[tid] / total, 1),
            "passes": int(t["passes"]),
            "pass_accuracy_pct": round(100 * t["passes_completed"] / passes, 1),
            "shots": int(t["shots"]),
            "shots_on_target": int(t["shots_on_target"]),
            "xg": round(t["xg"], 2),
            "big_chances": int(t["big_chances"]),
            "corners": int(t["corners"]),
            "fouls": int(t["fouls"]),
            "yellow_cards": int(t["yellow_cards"]),
            "red_cards": int(t["red_cards"]),
            "ball_wins": int(t["ball_wins"]),
            "high_press_wins": int(t["high_press_wins"]),
            "counter_attacks": len(counters[tid]),
        }
    return out
