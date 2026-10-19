"""Player facts and running player cards, computed only from events.

Each card snapshot is a Fact (`player-<id>@<event_id>`) so every card line on
screen cites figures the FactChecker can verify.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..agents.facts import Fact, MatchContext, _assist
from ..sim.schema import Event

TOUCH_TYPES = {"pass", "carry", "shot", "tackle", "interception", "clearance", "save",
               "kickoff", "free_kick", "corner"}


@dataclass
class PlayerTotals:
    touches: int = 0
    passes_completed: int = 0
    passes_attempted: int = 0
    shots: int = 0
    xg: float = 0.0
    goals: int = 0
    ball_wins: int = 0
    saves: int = 0
    key_passes: int = 0

    def figures(self) -> dict:
        return {"touches": self.touches, "passes_completed": self.passes_completed,
                "passes_attempted": self.passes_attempted, "shots": self.shots, "xg": round(self.xg, 2),
                "goals": self.goals, "ball_wins": self.ball_wins, "saves": self.saves,
                "key_passes": self.key_passes}


def key_pass_events(events: list[Event]) -> dict[int, int]:
    """shot event id -> the completed pass that set it up (same possession, received by the shooter)."""
    out = {}
    for e in events:
        if e.type == "shot":
            a = _assist(events, e)
            if a is not None:
                out[e.event_id] = a.event_id
    return out


def apply(t: PlayerTotals, e: Event, pid: str, key_pass_ids: set[int]) -> bool:
    """Update totals with one event; True if the event involved the player."""
    if e.player != pid:
        return False
    if e.type in TOUCH_TYPES:
        t.touches += 1
    if e.type == "pass":
        t.passes_attempted += 1
        t.passes_completed += e.outcome == "complete"
        t.key_passes += e.event_id in key_pass_ids
    elif e.type == "shot":
        t.shots += 1
        t.xg += e.xg or 0.0
    elif e.type == "goal":
        t.goals += 1
    elif e.type in ("tackle", "interception") and e.outcome == "won":
        t.ball_wins += 1
    elif e.type == "save":
        t.saves += 1
    return e.type != "half_end"


def totals_upto(events: list[Event], pid: str, upto_event: int) -> PlayerTotals:
    """Independent recomputation (used by tests and to cross-check snapshots)."""
    kp = set(key_pass_events(events).values())
    t = PlayerTotals()
    for e in events:
        if e.event_id > upto_event:
            break
        apply(t, e, pid, kp)
    return t


def involvement(events: list[Event]) -> Counter:
    kp = key_pass_events(events)
    score: Counter = Counter()
    passer = {e.event_id: e.player for e in events}
    for e in events:
        if e.type in ("shot", "goal", "save") and e.player:
            score[e.player] += 1
    for pass_id in kp.values():
        score[passer[pass_id]] += 1
    return score


def focus_players(events: list[Event], ctx: MatchContext, roles: dict[str, str], top: int = 6) -> list[str]:
    """Top players by involvement (shots + goals + key passes + saves), plus both starting goalkeepers."""
    inv = involvement(events)
    touches = Counter(e.player for e in events if e.player and e.type in TOUCH_TYPES)
    ranked = sorted((p for p in inv if p in ctx.player_names), key=lambda p: (-inv[p], -touches[p], p))
    chosen = ranked[:top]
    for team in (ctx.home, ctx.away):
        gk = next((pid for pid, r in roles.items() if r == "GK" and pid.startswith(team + "-")
                   and pid.endswith("-1")), None)
        if gk and gk not in chosen:
            chosen.append(gk)
    return chosen


@dataclass
class PlayerCard:
    player: str
    snapshots: list[dict] = field(default_factory=list)   # [{"at_event": id, **figures}]
    facts: dict[str, Fact] = field(default_factory=dict)   # card facts at key moments
    moments: list[tuple[int, str]] = field(default_factory=list)  # (event_id, reason)


def build_card(events: list[Event], ctx: MatchContext, pid: str) -> PlayerCard:
    kp = key_pass_events(events)
    kp_ids = set(kp.values())
    card = PlayerCard(pid)
    t = PlayerTotals()
    name, team = ctx.player_names[pid], pid.split("-")[0]
    for e in events:
        involved = apply(t, e, pid, kp_ids)
        if involved:
            card.snapshots.append({"at_event": e.event_id, **t.figures()})
        reason = None
        if e.player == pid and e.type in ("shot", "goal", "save"):
            reason = e.type
        elif e.player == pid and e.type == "pass" and e.event_id in kp_ids:
            reason = "key_pass"
        elif e.type == "half_end":
            reason = "half_time" if e.period == 1 else "full_time"
        if reason:
            fid = f"player-{pid}@{e.event_id}"
            card.facts[fid] = Fact(
                id=fid, kind="player_card",
                summary=f"{name}: {t.touches} touches, {t.passes_completed}/{t.passes_attempted} passes, "
                        f"{t.shots} shots ({t.xg:.2f} xG), {t.goals} goals, {t.ball_wins} ball wins, {t.saves} saves.",
                figures=t.figures(), players=[pid], teams=[team],
                evidence=[x.event_id for x in events if x.player == pid and x.event_id <= e.event_id][-12:],
                minute=e.minute)
            card.moments.append((e.event_id, reason))
    return card


def _n(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def card_text(f: Fact, ctx: MatchContext, profile: str, is_keeper: bool) -> str:
    """Card line built only from the card fact's figures (passes the FactChecker by construction)."""
    g = f.figures
    name = ctx.player_names[f.players[0]]
    if profile == "casual":
        if is_keeper:
            return f"{name} has made {g['saves']} saves so far." if g["saves"] != 1 else f"{name} has made a save so far."
        if g["goals"]:
            return f"{name} has scored {g['goals']} so far." if g["goals"] > 1 else f"{name} is on the scoresheet today."
        if g["shots"]:
            return f"{name} has had {g['shots']} shots so far." if g["shots"] > 1 else f"{name} has had a shot so far."
        return f"{name} has had {g['touches']} touches so far."
    if is_keeper:
        return (f"{name}: {_n(g['saves'], 'save')}, {_n(g['touches'], 'touch', 'touches')}, "
                f"{g['passes_completed']}/{g['passes_attempted']} passes.")
    return (f"{name}: {_n(g['touches'], 'touch', 'touches')}, {g['passes_completed']}/{g['passes_attempted']} passes, "
            f"{_n(g['shots'], 'shot')} ({g['xg']:.2f} xG), {_n(g['goals'], 'goal')}, "
            f"{_n(g['key_passes'], 'key pass', 'key passes')}, {_n(g['ball_wins'], 'ball win')}.")
