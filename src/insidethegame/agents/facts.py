"""Fact registry: the only source of truth agents may cite.

Every fact is computed deterministically from the event stream. Each one has
an id, a neutral summary, the figures it supports, the players it involves,
and the event ids that prove it. Agents write prose on top of facts and must
cite fact ids; the fact-checker rebuilds this registry independently and
rejects any claim whose numbers or names are not backed by the facts it cites.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..sim.engine import MatchResult
from ..sim.io import load_events
from ..sim.schema import Event
from ..sim.stats import team_stats

MOMENTUM_WINDOW_MIN = 15
SWING_SHARE_POINTS = 35  # share of attacking threat must move this many points


@dataclass(frozen=True)
class MatchContext:
    match_id: str
    home: str
    away: str
    team_names: dict[str, str]
    player_names: dict[str, str]

    @classmethod
    def from_result(cls, r: MatchResult) -> "MatchContext":
        return cls(
            match_id=r.match_id,
            home=r.home.id,
            away=r.away.id,
            team_names={t.id: t.name for t in (r.home, r.away)},
            player_names={p.id: p.name for t in (r.home, r.away) for p in t.players},
        )

    @classmethod
    def from_summary(cls, m: dict) -> "MatchContext":
        teams = (m["home"], m["away"])
        return cls(
            match_id=m["match_id"],
            home=m["home"]["id"],
            away=m["away"]["id"],
            team_names={t["id"]: t["name"] for t in teams},
            player_names={p["id"]: p["name"] for t in teams for p in t["starters"] + t["bench"]},
        )

    def other(self, team: str) -> str:
        return self.away if team == self.home else self.home


@dataclass
class Fact:
    id: str
    kind: str
    summary: str
    figures: dict[str, float | int | str] = field(default_factory=dict)
    players: list[str] = field(default_factory=list)   # player ids involved
    teams: list[str] = field(default_factory=list)
    evidence: list[int] = field(default_factory=list)  # event ids
    minute: int | None = None
    importance: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def load_match(match_dir: str | Path) -> tuple[list[Event], MatchContext]:
    match_dir = Path(match_dir)
    events = load_events(match_dir / "events.jsonl")
    with open(match_dir / "match.json", encoding="utf-8") as f:
        ctx = MatchContext.from_summary(json.load(f))
    return events, ctx


def build_facts(events: list[Event], ctx: MatchContext) -> dict[str, Fact]:
    facts: list[Fact] = []
    name = ctx.player_names.get
    team = ctx.team_names.get
    by_id = {e.event_id: e for e in events}

    # ---- goals, with running score and assist ---------------------------
    score = {ctx.home: 0, ctx.away: 0}
    goal_n = 0
    for e in events:
        if e.type != "goal":
            continue
        goal_n += 1
        score[e.team] += 1
        shot = by_id.get(e.related_event)
        assist = _assist(events, shot) if shot else None
        players = [e.player] + ([assist.player] if assist else [])
        evidence = [x.event_id for x in (assist, shot, e) if x]
        summary = (f"{name(e.player)} scored for {team(e.team)} at {e.display_clock} "
                   f"from a chance worth {e.xg:.2f} xG"
                   + (f", assisted by {name(assist.player)}" if assist else "")
                   + f". Score: {team(ctx.home)} {score[ctx.home]}-{score[ctx.away]} {team(ctx.away)}.")
        tags = set(e.tags) | (set(shot.tags) if shot else set())
        facts.append(Fact(
            id=f"goal-{goal_n}", kind="goal", summary=summary,
            figures={"xg": round(e.xg or 0, 2), "home_goals": score[ctx.home], "away_goals": score[ctx.away],
                     "display_clock": e.display_clock},
            players=players, teams=[e.team], evidence=evidence, minute=e.minute,
            importance=10 + (3 if (e.xg or 0) < 0.05 else 0) + (3 if e.minute >= 85 else 0)
            + (2 if "counter_attack" in tags else 0),
        ))

    # ---- final score ----------------------------------------------------
    goals = [e for e in events if e.type == "goal"]
    facts.append(Fact(
        id="final-score", kind="result",
        summary=f"Final score: {team(ctx.home)} {score[ctx.home]}-{score[ctx.away]} {team(ctx.away)}.",
        figures={"home_goals": score[ctx.home], "away_goals": score[ctx.away]},
        teams=[ctx.home, ctx.away], evidence=[g.event_id for g in goals], importance=20,
    ))

    # ---- team stats and xG story -----------------------------------------
    stats = team_stats(events, (ctx.home, ctx.away))
    for tid, s in stats.items():
        facts.append(Fact(
            id=f"stats-{tid}", kind="team_stats",
            summary=(f"{team(tid)}: {s['possession_pct']}% possession, {s['shots']} shots "
                     f"({s['shots_on_target']} on target), {s['xg']} xG, {s['pass_accuracy_pct']}% pass accuracy."),
            figures={k: v for k, v in s.items()}, teams=[tid], importance=6,
        ))
        diff = round(s["goals"] - s["xg"], 2)
        facts.append(Fact(
            id=f"xg-story-{tid}", kind="xg_vs_goals",
            summary=(f"{team(tid)} scored {s['goals']} from {s['xg']} xG, "
                     f"{'above' if diff > 0 else 'below' if diff < 0 else 'exactly in line with'} expectation"
                     + (f" by {abs(diff)}." if diff else ".")),
            figures={"goals": s["goals"], "xg": s["xg"], "difference": abs(diff)},
            teams=[tid], evidence=[e.event_id for e in events if e.type == "shot" and e.team == tid],
            importance=5 + 3 * min(abs(diff), 2),
        ))

    # ---- momentum by window, and swings ----------------------------------
    windows = _momentum(events, ctx)
    prev = None
    for w in windows:
        lead = max((ctx.home, ctx.away), key=lambda t: w["threat"][t])
        facts.append(Fact(
            id=f"momentum-{w['start']}-{w['end']}", kind="momentum_window",
            summary=(f"Minutes {w['start']}-{w['end']}: {team(lead)} had {w['share'][lead]}% of the attacking "
                     f"threat ({w['shots'][lead]} shots, {w['xg'][lead]} xG, "
                     f"{w['entries'][lead]} final-third entries)."),
            figures={"start_minute": w["start"], "end_minute": w["end"], "share_pct": w["share"][lead],
                     "shots": w["shots"][lead], "xg": w["xg"][lead], "final_third_entries": w["entries"][lead],
                     "other_shots": w["shots"][ctx.other(lead)], "other_xg": w["xg"][ctx.other(lead)]},
            teams=[lead], evidence=w["evidence"], minute=w["start"], importance=2,
        ))
        if prev is not None:
            delta = w["share"][ctx.home] - prev["share"][ctx.home]
            if abs(delta) >= SWING_SHARE_POINTS:
                gainer = ctx.home if delta > 0 else ctx.away
                facts.append(Fact(
                    id=f"swing-{w['start']}", kind="momentum_swing",
                    summary=(f"Momentum swung to {team(gainer)} from minute {w['start']}: their share of the "
                             f"attacking threat rose from {prev['share'][gainer]}% to {w['share'][gainer]}%."),
                    figures={"minute": w["start"], "share_before_pct": prev["share"][gainer],
                             "share_after_pct": w["share"][gainer], "change_points": abs(delta)},
                    teams=[gainer], evidence=w["evidence"], minute=w["start"], importance=4 + abs(delta) / 20,
                ))
        prev = w

    # ---- counterattacks that produced a shot ------------------------------
    poss = defaultdict(list)
    for e in events:
        poss[e.possession_id].append(e)
    c_n = 0
    for pid, evs in poss.items():
        if not any("counter_attack" in e.tags for e in evs):
            continue
        shot = next((e for e in evs if e.type == "shot"), None)
        if not shot:
            continue
        c_n += 1
        start = evs[0]
        secs = round(shot.clock - start.clock)
        passes = sum(e.type == "pass" and e.outcome == "complete" for e in evs)
        facts.append(Fact(
            id=f"counter-{c_n}", kind="counter_attack",
            summary=(f"{team(shot.team)} countered at {start.display_clock}: from winning the ball to "
                     f"{name(shot.player)}'s shot took {secs} seconds and {passes} passes "
                     f"(xG {shot.xg:.2f}, {shot.outcome.replace('_', ' ')})."),
            figures={"seconds": secs, "passes": passes, "xg": round(shot.xg or 0, 2), "outcome": shot.outcome,
                     "display_clock": start.display_clock},
            players=[shot.player], teams=[shot.team], evidence=[e.event_id for e in evs], minute=start.minute,
            importance=5 + (5 if shot.outcome == "goal" else 0),
        ))

    # ---- pressing ---------------------------------------------------------
    for tid in (ctx.home, ctx.away):
        wins = [e for e in events if e.team == tid and "high_press_win" in e.tags]
        led_to_shot = []
        for w in wins:
            nxt = [e for e in poss[w.possession_id + 1]] if w.possession_id + 1 in poss else []
            # The win ends the opponent's possession; the next possession is ours.
            if any(e.type == "shot" and e.team == tid for e in nxt):
                led_to_shot.append(w)
        facts.append(Fact(
            id=f"press-{tid}", kind="pressing",
            summary=(f"{team(tid)} won the ball back high up the pitch {len(wins)} times; "
                     f"{len(led_to_shot)} of those led straight to a shot."),
            figures={"high_press_wins": len(wins), "led_to_shot": len(led_to_shot)},
            teams=[tid], evidence=[w.event_id for w in wins], importance=3 + len(led_to_shot),
        ))

    # ---- big chances missed ------------------------------------------------
    for i, s in enumerate((e for e in events if e.type == "shot" and (e.xg or 0) >= 0.30 and e.outcome != "goal"), 1):
        facts.append(Fact(
            id=f"big-miss-{i}", kind="big_chance_missed",
            summary=(f"{name(s.player)} ({team(s.team)}) missed a big chance at {s.display_clock}: "
                     f"{s.xg:.2f} xG, {s.outcome.replace('_', ' ')}."),
            figures={"xg": round(s.xg, 2), "outcome": s.outcome, "display_clock": s.display_clock},
            players=[s.player], teams=[s.team], evidence=[s.event_id], minute=s.minute,
            importance=4 + 4 * s.xg,
        ))

    # ---- goalkeepers -------------------------------------------------------
    for tid in (ctx.home, ctx.away):
        opp = ctx.other(tid)
        on_target = [e for e in events if e.type == "shot" and e.team == opp and e.outcome in ("goal", "saved")]
        saves = [e for e in events if e.type == "save" and e.team == tid]
        conceded = sum(e.outcome == "goal" for e in on_target)
        xg_faced = round(sum(e.xg or 0 for e in on_target), 2)
        prevented = round(xg_faced - conceded, 2)
        keeper = saves[0].player if saves else None
        facts.append(Fact(
            id=f"keeper-{tid}", kind="goalkeeping",
            summary=(f"{team(tid)}'s goalkeeper{(' ' + name(keeper)) if keeper else ''} made {len(saves)} saves, "
                     f"facing {xg_faced} xG on target and conceding {conceded}."),
            figures={"saves": len(saves), "xg_on_target_faced": xg_faced, "conceded": conceded,
                     "goals_prevented": prevented},
            players=[keeper] if keeper else [], teams=[tid], evidence=[e.event_id for e in saves + on_target],
            importance=3 + max(prevented, 0) * 3,
        ))

    # ---- red cards -----------------------------------------------------------
    for i, c in enumerate((e for e in events if e.type == "card" and e.outcome == "red"), 1):
        facts.append(Fact(
            id=f"red-{i}", kind="red_card",
            summary=(f"{name(c.player)} of {team(c.team)} was sent off at {c.display_clock}"
                     + (" for a second yellow." if "second_yellow" in c.tags else ".")),
            figures={"display_clock": c.display_clock}, players=[c.player], teams=[c.team],
            evidence=[c.event_id] + ([c.related_event] if c.related_event else []), minute=c.minute, importance=9,
        ))

    # ---- top shooter --------------------------------------------------------
    by_player = defaultdict(lambda: {"shots": 0, "xg": 0.0, "goals": 0, "ids": []})
    for e in events:
        if e.type == "shot":
            p = by_player[(e.team, e.player)]
            p["shots"] += 1
            p["xg"] += e.xg or 0
            p["goals"] += e.outcome == "goal"
            p["ids"].append(e.event_id)
    if by_player:
        (tid, pid), p = max(by_player.items(), key=lambda kv: (kv[1]["xg"], kv[1]["shots"]))
        facts.append(Fact(
            id="top-threat", kind="player_threat",
            summary=(f"{name(pid)} ({team(tid)}) was the biggest threat: {p['shots']} shots worth "
                     f"{p['xg']:.2f} xG, {p['goals']} scored."),
            figures={"shots": p["shots"], "xg": round(p["xg"], 2), "goals": p["goals"]},
            players=[pid], teams=[tid], evidence=p["ids"], importance=5,
        ))

    return {f.id: f for f in facts}


def _assist(events: list[Event], shot: Event) -> Event | None:
    for e in reversed(events[: shot.event_id - 1]):
        if e.possession_id != shot.possession_id:
            return None
        if e.type == "pass" and e.outcome == "complete" and e.secondary_player == shot.player:
            return e
        if e.type in ("pass", "carry") and e.player != shot.player:
            return None
    return None


def _momentum(events: list[Event], ctx: MatchContext) -> list[dict]:
    """Attacking threat per window: xG plus a small weight for final-third entries."""
    windows = []
    last_minute = max(e.minute for e in events)
    for start in range(0, last_minute + 1, MOMENTUM_WINDOW_MIN):
        end = start + MOMENTUM_WINDOW_MIN
        if start >= 90:
            break
        if end == 90:
            end = last_minute + 1  # fold stoppage time into the final window
        w = {"start": start, "end": end, "shots": {}, "xg": {}, "entries": {}, "threat": {}, "share": {},
             "evidence": []}
        in_win = [e for e in events if start <= e.minute < end]
        for tid in (ctx.home, ctx.away):
            shots = [e for e in in_win if e.team == tid and e.type == "shot"]
            entries = [e for e in in_win if e.team == tid and e.type in ("pass", "carry")
                       and e.outcome == "complete" and (e.x or 0) < 70 <= (e.end_x or 0)]
            w["shots"][tid] = len(shots)
            w["xg"][tid] = round(sum(e.xg or 0 for e in shots), 2)
            w["entries"][tid] = len(entries)
            w["threat"][tid] = w["xg"][tid] + 0.02 * len(entries)
            w["evidence"] += [e.event_id for e in shots]
        total = sum(w["threat"].values()) or 1
        share_home = round(100 * w["threat"][ctx.home] / total)
        w["share"] = {ctx.home: share_home, ctx.away: 100 - share_home}
        w["evidence"].sort()
        windows.append(w)
        if end > 90:
            break
    return windows
