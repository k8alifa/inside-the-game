"""Possession-based synthetic match simulator.

The engine plays a match as a chain of possessions. Each possession is a run
of passes and carries that ends in a turnover, a foul, or a shot. Team style
knobs (see teams.TeamProfile) shape how far the ball travels per action, how
often it is lost, where it is won back, and how good the chances are.

Output is deterministic for a given seed. It is synthetic and only aims to be
football-realistic in its aggregate shape (shots, xG, pass completion, goals),
not to reproduce any real match.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .config import SimConfig
from .schema import GOAL_WIDTH, HALF_LENGTH_S, PITCH_LENGTH, PITCH_WIDTH, Event
from .teams import Player, TeamProfile

GENERATOR_VERSION = "0.1.0"
GOAL_X, GOAL_Y = PITCH_LENGTH, PITCH_WIDTH / 2


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def xg_model(x: float, y: float, header: bool = False) -> float:
    """Logistic xG from distance and the angle the goal mouth subtends."""
    dx, dy = GOAL_X - x, GOAL_Y - y
    dist = math.hypot(dx, dy)
    a1 = math.atan2(GOAL_Y + GOAL_WIDTH / 2 - y, dx)
    a2 = math.atan2(GOAL_Y - GOAL_WIDTH / 2 - y, dx)
    angle = abs(a1 - a2)
    z = -1.0 - 0.12 * dist + 1.2 * angle - (0.9 if header else 0.0)
    return _clamp(1 / (1 + math.exp(-z)), 0.01, 0.95)


@dataclass
class _Side:
    profile: TeamProfile
    on_pitch: list[Player]
    bench: list[Player]
    sub_minutes: list[float]
    yellows: dict[str, int] = field(default_factory=dict)
    sent_off: int = 0
    goals: int = 0

    @property
    def id(self) -> str:
        return self.profile.id

    def strength(self, knob: str) -> float:
        # A red card costs a team about 12% on every knob.
        return getattr(self.profile, knob) * (0.88 ** self.sent_off)


@dataclass
class MatchResult:
    match_id: str
    seed: int
    home: TeamProfile
    away: TeamProfile
    events: list[Event]
    lineup_changes: list[dict]
    config: SimConfig | None = None

    @property
    def score(self) -> dict[str, int]:
        s = {self.home.id: 0, self.away.id: 0}
        for e in self.events:
            if e.type == "goal":
                s[e.team] += 1
        return s


class MatchSimulator:
    def __init__(self, home: TeamProfile, away: TeamProfile, seed: int, match_id: str | None = None,
                 config: SimConfig | None = None):
        if home.id == away.id:
            raise ValueError("home and away must be different teams")
        self.rng = random.Random(seed)
        self.seed = seed
        self.match_id = match_id or f"{home.id}-{away.id}-{seed}"
        self.sides = {t.id: self._side(t) for t in (home, away)}
        self.home, self.away = home, away
        self.cfg = config or SimConfig()
        self.events: list[Event] = []
        self.lineup_changes: list[dict] = []
        self.period = 1
        self.clock = 0.0
        self.period_end = 0.0
        self.poss_id = 0

    # ------------------------------------------------------------------ setup
    def _side(self, t: TeamProfile) -> _Side:
        subs = sorted(self.rng.uniform(55, 86) for _ in range(3))
        return _Side(profile=t, on_pitch=list(t.starters), bench=list(t.bench), sub_minutes=subs)

    def _other(self, team_id: str) -> _Side:
        return self.sides[self.away.id if team_id == self.home.id else self.home.id]

    # ---------------------------------------------------------------- helpers
    def _emit(self, team: str, player: Player | None, type_: str, **kw) -> Event:
        base = 0 if self.period == 1 else 45
        if self.clock >= HALF_LENGTH_S:
            extra = int((self.clock - HALF_LENGTH_S) // 60) + 1
            display = f"{base + 45}+{extra}'"
        else:
            display = f"{base + int(self.clock // 60) + 1}'"
        ev = Event(
            match_id=self.match_id,
            event_id=len(self.events) + 1,
            period=self.period,
            clock=self.clock,
            minute=base + int(self.clock // 60),
            display_clock=display,
            team=team,
            player=player.id if player else None,
            type=type_,
            possession_id=self.poss_id,
            **kw,
        )
        self.events.append(ev)
        return ev

    def _pick(self, side: _Side, x: float, exclude: Player | None = None) -> Player:
        """Pick a player likely to be on the ball at depth x (team frame)."""
        if x < 35:
            w = {"GK": 1.0 if x < 15 else 0.0, "DF": 5, "MF": 3, "FW": 0.4}
        elif x < 70:
            w = {"GK": 0.0, "DF": 2, "MF": 5, "FW": 2}
        else:
            w = {"GK": 0.0, "DF": 0.5, "MF": 3, "FW": 5}
        pool = [p for p in side.on_pitch if p is not exclude and w[p.role] > 0]
        if not pool:
            pool = [p for p in side.on_pitch if p is not exclude]
        return self.rng.choices(pool, weights=[w[p.role] or 0.1 for p in pool])[0]

    def _home_factor(self, side: _Side) -> float:
        return 1.0 + self.cfg.home_advantage if side.id == self.home.id else 1.0

    def _gk(self, side: _Side) -> Player:
        return next((p for p in side.on_pitch if p.role == "GK"), side.on_pitch[0])

    def _tick(self, lo: float, hi: float) -> None:
        self.clock += self.rng.uniform(lo, hi)

    # ------------------------------------------------------------- match flow
    def run(self) -> MatchResult:
        for period, kicker in ((1, self.home.id), (2, self.away.id)):
            self.period = period
            self.clock = 0.0
            self.period_end = HALF_LENGTH_S + self.rng.uniform(60, 150 if period == 1 else 330)
            state = (kicker, PITCH_LENGTH / 2, PITCH_WIDTH / 2, "kickoff")
            while self.clock < self.period_end:
                self._maybe_substitute()
                state = self._possession(*state)
            self._emit(self.home.id, None, "half_end")
        return MatchResult(self.match_id, self.seed, self.home, self.away, self.events, self.lineup_changes,
                           self.cfg)

    def _maybe_substitute(self) -> None:
        if self.period != 2:
            return
        minute = 45 + self.clock / 60
        for side in self.sides.values():
            while side.sub_minutes and minute >= side.sub_minutes[0] and side.bench:
                side.sub_minutes.pop(0)
                outfield = [p for p in side.on_pitch if p.role != "GK"]
                off = self.rng.choice(outfield)
                same_role = [p for p in side.bench if p.role == off.role]
                on = (same_role or [p for p in side.bench if p.role != "GK"] or side.bench)[0]
                side.bench.remove(on)
                side.on_pitch[side.on_pitch.index(off)] = on
                self._emit(side.id, off, "substitution", secondary_player=on.id)
                self.lineup_changes.append({"team": side.id, "minute": int(minute), "off": off.id, "on": on.id})
                self._tick(20, 40)

    def _possession(self, team_id: str, x: float, y: float, kind: str):
        self.poss_id += 1
        att, dfn = self.sides[team_id], self._other(team_id)
        start_idx, start_clock, start_x = len(self.events), self.clock, x
        poss_tags: list[str] = []
        self._tag_from: dict[str, int] = {}  # tag -> first event index it may appear on
        if kind in ("corner", "free_kick"):
            poss_tags.append("set_piece")

        if kind == "kickoff":
            self._emit(att.id, self._pick(att, 50), "kickoff", x=x, y=y)
        elif kind == "corner":
            result = self._corner(att, dfn, y)
            self._tag(start_idx, poss_tags)
            return result
        elif kind == "free_kick":
            result = self._free_kick(att, dfn, x, y)
            if result is not None:
                self._tag(start_idx, poss_tags)
                return result

        actions = 0
        carrier = self._pick(att, x)
        while True:
            if self.clock >= self.period_end:
                self._tag(start_idx, poss_tags)
                return (dfn.id, PITCH_LENGTH / 2, PITCH_WIDTH / 2, "kickoff")
            actions += 1

            # Counterattack: won within 40 m of own goal, final 30 m within 12 s and 5 actions.
            # The tag starts at the action that completed the counter, never earlier.
            if (kind == "turnover" and start_x < 40 and x >= 75
                    and self.clock - start_clock <= 12 and actions <= 5
                    and "counter_attack" not in poss_tags):
                poss_tags.append("counter_attack")
                self._tag_from["counter_attack"] = len(self.events) - 1

            # Shot decision in the final third, more likely closer and central.
            if x >= 78:
                centrality = 1 - abs(y - GOAL_Y) / GOAL_Y
                c = self.cfg
                p_shot = (c.shot_base + c.shot_slope * ((x - 78) / 27) ** 1.5) * (0.2 + 1.3 * centrality) * (0.7 + 0.6 * att.strength("attack"))
                p_shot *= self._home_factor(att)
                if self.rng.random() < p_shot:
                    result = self._shot(att, dfn, carrier, x, y, poss_tags)
                    self._tag(start_idx, poss_tags)
                    return result

            # Defensive foul.
            if self.rng.random() < self.cfg.foul_base * (1.45 - dfn.strength("discipline")) * (1 + 0.6 * dfn.strength("press")):
                result = self._foul(att, dfn, carrier, x, y)
                self._tag(start_idx, poss_tags)
                return result

            # Pass or carry forward.
            directness = att.strength("directness")
            carry = self.rng.random() < 0.20
            fwd = self.rng.gauss(2.5 + 9 * directness if not carry else 6, 7 if not carry else 3)
            if x > 88:
                fwd = min(fwd, 4)
            elif x > 72 and self.rng.random() < 0.25:
                fwd = -abs(self.rng.gauss(10, 5))  # recycle the ball backward
            nx = _clamp(x + fwd, 2, 99)  # tap-ins from inside 6 m are rare
            ny = _clamp(y + self.rng.gauss(0, 11 if not carry else 4), 2, 66)
            depth = nx / PITCH_LENGTH
            press_here = dfn.strength("press") * (1.4 if x < 40 else 0.7)  # pressing bites in the build-up zone
            p_loss = (self.cfg.pass_loss_base + 0.20 * depth ** 2 + 0.08 * directness) * (0.55 + 0.75 * press_here) / (0.7 + 0.6 * att.strength("technique"))
            if carry:
                p_loss *= 0.6
            p_loss *= 0.85 + 0.3 * dfn.strength("defense")
            self._tick(1.8, 3.4 + 3.2 * (1 - att.strength("tempo")))

            if self.rng.random() < p_loss:
                gx, gy = PITCH_LENGTH - nx, PITCH_WIDTH - ny
                winner = self._pick(dfn, gx)
                if carry:
                    self._emit(att.id, carrier, "carry", outcome="dispossessed", x=x, y=y, end_x=nx, end_y=ny)
                    win_type = "tackle"
                else:
                    self._emit(att.id, carrier, "pass", outcome="incomplete", x=x, y=y, end_x=nx, end_y=ny)
                    if nx >= 88 and self.rng.random() < self.cfg.corner_clearance:
                        # Defender clears it behind for a corner.
                        cleaner = self._pick(dfn, gx)
                        self._emit(dfn.id, cleaner, "clearance", outcome="out_for_corner", x=gx, y=gy)
                        self._tick(10, 20)
                        self._tag(start_idx, poss_tags)
                        return (att.id, PITCH_LENGTH, 0.5 if ny < GOAL_Y else PITCH_WIDTH - 0.5, "corner")
                    win_type = "interception"
                tags = ["high_press_win"] if gx >= 70 else []
                self._emit(dfn.id, winner, win_type, outcome="won", x=gx, y=gy, tags=tags)
                self._tag(start_idx, poss_tags)
                return (dfn.id, gx, gy, "turnover")

            if carry:
                self._emit(att.id, carrier, "carry", outcome="complete", x=x, y=y, end_x=nx, end_y=ny)
            else:
                receiver = self._pick(att, nx, exclude=carrier)
                self._emit(att.id, carrier, "pass", outcome="complete", x=x, y=y, end_x=nx, end_y=ny,
                           secondary_player=receiver.id)
                carrier = receiver
            x, y = nx, ny

    def _tag(self, start_idx: int, tags: list[str]) -> None:
        if not tags:
            return
        tag_from = getattr(self, "_tag_from", {})
        for i in range(start_idx, len(self.events)):
            ev = self.events[i]
            if ev.possession_id == self.poss_id:
                for t in tags:
                    if i >= tag_from.get(t, start_idx) and t not in ev.tags:
                        ev.tags.append(t)

    # ------------------------------------------------------------ set plays
    def _shot(self, att: _Side, dfn: _Side, shooter: Player, x: float, y: float, poss_tags: list[str],
              header: bool = False, xg_scale: float = 1.0):
        if shooter.role in ("GK", "DF") and self.rng.random() < 0.7:
            shooter = self._pick(att, 90)
        xg = xg_model(x, y, header) * xg_scale
        tags = (["header"] if header else []) + (["big_chance"] if xg >= 0.30 else [])
        shot = self._emit(att.id, shooter, "shot", x=x, y=y, end_x=GOAL_X, end_y=GOAL_Y, xg=xg, tags=tags)
        finish = xg * (0.85 + 0.3 * att.strength("attack")) * (1.15 - 0.3 * dfn.strength("defense"))
        finish *= self.cfg.finish_scale * self._home_factor(att)
        r = self.rng.random()
        if r < finish:
            shot.outcome = "goal"
            att.goals += 1
            self._emit(att.id, shooter, "goal", x=x, y=y, related_event=shot.event_id, xg=xg, tags=list(tags))
            self._tick(50, 80)
            return (dfn.id, PITCH_LENGTH / 2, PITCH_WIDTH / 2, "kickoff")
        r2 = self.rng.random()
        if r2 < 0.30:
            shot.outcome = "saved"
            gk = self._gk(dfn)
            self._emit(dfn.id, gk, "save", x=PITCH_LENGTH - GOAL_X + 1, y=PITCH_WIDTH - y, related_event=shot.event_id)
        elif r2 < 0.62:
            shot.outcome = "blocked"
        else:
            shot.outcome = "off_target"
            self._tick(20, 35)
            return (dfn.id, 6.0, PITCH_WIDTH / 2, "goal_kick")
        self._tick(8, 20)
        if self.rng.random() < self.cfg.corner_after_shot:
            return (att.id, PITCH_LENGTH, 0.5 if y < GOAL_Y else PITCH_WIDTH - 0.5, "corner")
        return (dfn.id, self.rng.uniform(8, 25), self.rng.uniform(10, 58), "turnover")

    def _corner(self, att: _Side, dfn: _Side, y: float):
        taker = self._pick(att, 80)
        tx, ty = self.rng.uniform(93, 101), self.rng.uniform(28, 40)
        self._emit(att.id, taker, "corner", x=PITCH_LENGTH - 0.5, y=y, end_x=tx, end_y=ty)
        self._tick(2, 4)
        if self.rng.random() < 0.30:
            target = self._pick(att, 100, exclude=taker)
            return self._shot(att, dfn, target, tx, ty, ["set_piece"], header=True)
        cleaner = self._pick(dfn, PITCH_LENGTH - tx)
        cx, cy = PITCH_LENGTH - tx, PITCH_WIDTH - ty
        self._emit(dfn.id, cleaner, "clearance", x=cx, y=cy, end_x=cx + self.rng.uniform(20, 40), end_y=cy)
        return (dfn.id, cx + 25, cy, "turnover")

    def _foul(self, att: _Side, dfn: _Side, victim: Player, x: float, y: float):
        fx, fy = PITCH_LENGTH - x, PITCH_WIDTH - y
        offender = self._pick(dfn, fx)
        foul = self._emit(dfn.id, offender, "foul", x=fx, y=fy, secondary_player=victim.id)
        p_card = self.cfg.card_base * (1.4 - dfn.strength("discipline")) * (1.3 if x > 70 else 1.0)
        if dfn.yellows.get(offender.id):
            p_card *= 0.3  # booked players pull out of challenges
        if self.rng.random() < p_card:
            n = dfn.yellows.get(offender.id, 0) + 1
            dfn.yellows[offender.id] = n
            if n == 2:
                self._emit(dfn.id, offender, "card", outcome="red", related_event=foul.event_id, tags=["second_yellow"])
                dfn.on_pitch.remove(offender)
                dfn.sent_off += 1
            else:
                self._emit(dfn.id, offender, "card", outcome="yellow", related_event=foul.event_id)
        self._tick(15, 40)
        return (att.id, x, y, "free_kick")

    def _free_kick(self, att: _Side, dfn: _Side, x: float, y: float):
        taker = self._pick(att, max(x, 70))
        if x >= 74 and abs(y - GOAL_Y) < 18 and self.rng.random() < 0.45:
            self._emit(att.id, taker, "free_kick", outcome="direct", x=x, y=y)
            return self._shot(att, dfn, taker, x, y, ["set_piece"], xg_scale=0.75)
        self._emit(att.id, taker, "free_kick", outcome="short", x=x, y=y)
        return None
