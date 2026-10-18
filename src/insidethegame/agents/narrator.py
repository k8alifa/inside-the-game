"""Live narrator: real-time commentary from a streaming event feed.

    feed event -> MomentDetector -> Moment (a live Fact) -> narrator -> checker -> line

The detector watches events one at a time and only knows the past. When
something worth saying happens (goal, save, big miss, card, substitution,
momentum swing, half-time, full-time) it builds a Fact for that moment from
the events seen so far.

Live commentary cannot wait for a revision round, so the safety rule is
different from the post-match analyst: if the model's line fails the check,
the narrator says the template line instead, which is built only from the
moment's figures and always passes. Every line carries its fact ids and
event ids.

Note on synthetic tags: the simulator writes possession tags such as
counter_attack onto every event in the possession once it ends, so a replayed
feed carries them a few seconds early. A real feed would add them at the shot.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable

from ..sim.schema import Event
from ..sim.stats import team_stats
from .analyst import parse_claims
from .checker import Claim, FactChecker
from .facts import SWING_SHARE_POINTS, Fact, MatchContext, _assist, _momentum

log = logging.getLogger("insidethegame.agents")

SAVE_XG_MIN = 0.12      # saves worth mentioning
MISS_XG_MIN = 0.30      # misses worth mentioning (a big chance)


@dataclass
class Line:
    display_clock: str
    text: str
    fact_ids: list[str]
    evidence: list[int]
    source: str               # "model", "template", or "template-fallback"
    kind: str
    blocked: list[str] = field(default_factory=list)  # why the model line was not used
    at_event: int | None = None   # event id that triggered the line (for replay timing)
    period: int | None = None
    clock: float | None = None    # seconds into the period
    profile: str = "analyst"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class MomentDetector:
    def __init__(self, ctx: MatchContext):
        self.ctx = ctx
        self.events: list[Event] = []
        self.by_id: dict[int, Event] = {}
        self.score = {ctx.home: 0, ctx.away: 0}
        self.next_window = 30  # first swing check needs two complete 15-minute windows
        self.reported_swings: set[int] = set()
        self.facts: dict[str, Fact] = {}

    # Names and teams used in summaries.
    def _n(self, pid):
        return self.ctx.player_names.get(pid, pid)

    def _t(self, tid):
        return self.ctx.team_names.get(tid, tid)

    def _scoreline(self) -> str:
        c = self.ctx
        return f"{self._t(c.home)} {self.score[c.home]}-{self.score[c.away]} {self._t(c.away)}"

    def _score_figs(self) -> dict:
        return {"home_goals": self.score[self.ctx.home], "away_goals": self.score[self.ctx.away]}

    def feed(self, e: Event) -> list[Fact]:
        self.events.append(e)
        self.by_id[e.event_id] = e
        moments: list[Fact] = []

        swing = self._check_swing(e)
        if swing:
            moments.append(swing)

        m = None
        if e.type == "kickoff" and e.event_id == 1:
            m = Fact(id="kickoff", kind="kickoff",
                     summary=f"Kickoff: {self._t(self.ctx.home)} against {self._t(self.ctx.away)}.",
                     teams=[self.ctx.home, self.ctx.away], evidence=[e.event_id], minute=0)
        elif e.type == "goal":
            self.score[e.team] += 1
            m = self._goal(e)
        elif e.type == "save":
            shot = self.by_id.get(e.related_event)
            if shot and (shot.xg or 0) >= SAVE_XG_MIN:
                m = self._save(e, shot)
        elif e.type == "shot" and e.outcome in ("off_target", "blocked") and (e.xg or 0) >= MISS_XG_MIN:
            m = self._miss(e)
        elif e.type == "card":
            m = Fact(id=f"moment-{e.event_id}", kind=f"{e.outcome}_card",
                     summary=(f"{e.outcome.capitalize()} card for {self._n(e.player)} of {self._t(e.team)} "
                              f"at {e.display_clock}" + (", his second booking." if "second_yellow" in e.tags else ".")),
                     figures={"display_clock": e.display_clock}, players=[e.player], teams=[e.team],
                     evidence=[e.event_id] + ([e.related_event] if e.related_event else []), minute=e.minute)
        elif e.type == "substitution":
            m = Fact(id=f"moment-{e.event_id}", kind="substitution",
                     summary=(f"Substitution for {self._t(e.team)} at {e.display_clock}: "
                              f"{self._n(e.secondary_player)} on, {self._n(e.player)} off."),
                     figures={"display_clock": e.display_clock}, players=[e.player, e.secondary_player],
                     teams=[e.team], evidence=[e.event_id], minute=e.minute)
        elif e.type == "half_end":
            m = self._half_end(e)
        if m:
            moments.append(m)
        for f in moments:
            self.facts[f.id] = f
        return moments

    # ------------------------------------------------------------- moments
    def _counter_figs(self, shot: Event) -> tuple[dict, list[int]]:
        if "counter_attack" not in shot.tags:
            return {}, []
        poss = [x for x in self.events if x.possession_id == shot.possession_id]
        secs = round(shot.clock - poss[0].clock)
        passes = sum(x.type == "pass" and x.outcome == "complete" for x in poss)
        return {"counter_seconds": secs, "counter_passes": passes}, [x.event_id for x in poss]

    def _goal(self, g: Event) -> Fact:
        shot = self.by_id.get(g.related_event)
        assist = _assist(self.events, shot) if shot else None
        cfig, cev = self._counter_figs(shot) if shot else ({}, [])
        parts = [f"{self._n(g.player)} scored for {self._t(g.team)} at {g.display_clock}"]
        if assist:
            parts.append(f", set up by {self._n(assist.player)}")
        parts.append(f". The chance was worth {g.xg:.2f} xG.")
        if cfig:
            parts.append(f" It came from a counter: {cfig['counter_seconds']} seconds and "
                         f"{cfig['counter_passes']} passes from winning the ball.")
        parts.append(f" {self._scoreline()}.")
        return Fact(id=f"moment-{g.event_id}", kind="goal", summary="".join(parts),
                    figures={"xg": round(g.xg or 0, 2), "display_clock": g.display_clock, **self._score_figs(), **cfig},
                    players=[g.player] + ([assist.player] if assist else []), teams=[g.team],
                    evidence=sorted({g.event_id, *(x.event_id for x in (shot, assist) if x), *cev}), minute=g.minute)

    def _save(self, s: Event, shot: Event) -> Fact:
        return Fact(id=f"moment-{s.event_id}", kind="save",
                    summary=(f"{self._n(s.player)} saved from {self._n(shot.player)} at {shot.display_clock}, "
                             f"a chance worth {shot.xg:.2f} xG."),
                    figures={"xg": round(shot.xg or 0, 2), "display_clock": shot.display_clock},
                    players=[s.player, shot.player], teams=[s.team, shot.team],
                    evidence=[shot.event_id, s.event_id], minute=shot.minute)

    def _miss(self, shot: Event) -> Fact:
        how = "blocked" if shot.outcome == "blocked" else "off target"
        return Fact(id=f"moment-{shot.event_id}", kind="big_chance_missed",
                    summary=(f"Big chance for {self._n(shot.player)} of {self._t(shot.team)} at {shot.display_clock}: "
                             f"{shot.xg:.2f} xG, {how}."),
                    figures={"xg": round(shot.xg or 0, 2), "display_clock": shot.display_clock, "outcome": shot.outcome},
                    players=[shot.player], teams=[shot.team], evidence=[shot.event_id], minute=shot.minute)

    def _half_end(self, e: Event) -> Fact:
        ft = e.period == 2
        stats = team_stats(self.events, (self.ctx.home, self.ctx.away))
        h, a = stats[self.ctx.home], stats[self.ctx.away]
        label = "Full time" if ft else "Half-time"
        return Fact(id=f"{'full' if ft else 'half'}-time", kind="full_time" if ft else "half_time",
                    summary=(f"{label}: {self._scoreline()}. Shots {h['shots']}-{a['shots']}, "
                             f"xG {h['xg']}-{a['xg']}, possession {h['possession_pct']}%-{a['possession_pct']}%."),
                    figures={**self._score_figs(), "home_shots": h["shots"], "away_shots": a["shots"],
                             "home_xg": h["xg"], "away_xg": a["xg"],
                             "home_possession_pct": h["possession_pct"], "away_possession_pct": a["possession_pct"]},
                    teams=[self.ctx.home, self.ctx.away],
                    evidence=[x.event_id for x in self.events if x.type == "goal"] + [e.event_id], minute=e.minute)

    def _check_swing(self, e: Event) -> Fact | None:
        if e.period != 2 and e.minute >= 45:
            return None  # first-half stoppage: wait for the second half
        if e.minute < self.next_window:
            return None
        boundary = self.next_window
        self.next_window += 15
        windows = [w for w in _momentum(self.events[:-1], self.ctx) if w["end"] <= boundary]
        if len(windows) < 2:
            return None
        prev, cur = windows[-2], windows[-1]
        delta = cur["share"][self.ctx.home] - prev["share"][self.ctx.home]
        if abs(delta) < SWING_SHARE_POINTS:
            return None
        gainer = self.ctx.home if delta > 0 else self.ctx.away
        return Fact(id=f"swing-{cur['start']}", kind="momentum_swing",
                    summary=(f"Momentum check: between minutes {cur['start']} and {cur['end']}, {self._t(gainer)} "
                             f"had {cur['share'][gainer]}% of the attacking threat, up from {prev['share'][gainer]}%."),
                    figures={"start_minute": cur["start"], "end_minute": cur["end"],
                             "share_after_pct": cur["share"][gainer], "share_before_pct": prev["share"][gainer]},
                    teams=[gainer], evidence=cur["evidence"], minute=cur["start"])


# ------------------------------------------------------------- narrators
_GOAL_OPENERS = ("GOAL!", "It's in!", "What a moment!")


def _scoreline(f: Fact, ctx: MatchContext) -> str:
    h, a = f.figures.get("home_goals"), f.figures.get("away_goals")
    return f"{ctx.team_names[ctx.home]} {h}-{a} {ctx.team_names[ctx.away]}"


def template_line(f: Fact, ctx: MatchContext, profile: str = "analyst") -> str:
    """Broadcast phrasing built only from the fact's own words and figures."""
    rng = random.Random(f.id)
    name = lambda i: ctx.player_names.get(f.players[i], f.players[i]) if len(f.players) > i else ""
    team = ctx.team_names.get(f.teams[0], f.teams[0]) if f.teams else ""
    if profile == "casual":
        if f.kind == "goal":
            return f"{rng.choice(_GOAL_OPENERS)} {name(0)} scores for {team}! {_scoreline(f, ctx)}."
        if f.kind == "save":
            return f"Great save by {name(0)}! {name(1)} is denied."
        if f.kind == "big_chance_missed":
            return f"So close! {name(0)} should have scored for {team}."
        if f.kind in ("half_time", "full_time"):
            label = "Full time" if f.kind == "full_time" else "Half-time"
            return f"{label}: {_scoreline(f, ctx)}."
        if f.kind == "momentum_swing":
            return f"{team} are on top now and pushing forward."
        return f.summary
    if f.kind == "goal":
        return f"{rng.choice(_GOAL_OPENERS)} {f.summary}"
    if f.kind == "save":
        return f"What a stop! {f.summary}"
    if f.kind == "big_chance_missed":
        return f"He has to score there. {f.summary}"
    return f.summary


NARRATOR_INSTRUCTIONS = """\
You are a live football commentator. Write ONE line of commentary, at most 30
words, for each moment described. Energetic but accurate.

Rules:
- Use only the facts given for that moment. Every number must appear in them.
- Only name players and teams that appear in those facts.
- Do not repeat earlier lines.
"""
_ONE = 'Return ONLY JSON: {"text": "...", "fact_ids": ["<the moment fact id>"]}'
_MANY = ('Return ONLY JSON: {"lines": [{"moment": "<fact id>", "text": "...", '
         '"fact_ids": ["<fact id>"]}]}, one entry per moment, same order.')


class TemplateNarrator:
    def __init__(self, profile: str = "analyst"):
        self.profile = profile

    async def say(self, moment: Fact, ctx: MatchContext, recent: list[str]) -> Claim:
        return Claim(text=template_line(moment, ctx, self.profile), fact_ids=[moment.id], kind=moment.kind,
                     source="template")

    async def say_batch(self, moments: list[Fact], ctx: MatchContext, recent: list[str]) -> dict[str, Claim]:
        return {m.id: await self.say(m, ctx, recent) for m in moments}


def _moment_block(m: Fact) -> str:
    return f"- {m.id} [{m.kind}]: {m.summary} Figures: {json.dumps(m.figures)}"


class LLMNarrator:
    def __init__(self, client, profile: str = "analyst"):
        from agent_framework import Agent

        from .analyst import PROFILES

        self.profile = profile
        self.agent = Agent(client=client, name="narrator",
                           instructions=NARRATOR_INSTRUCTIONS + PROFILES.get(profile, PROFILES["analyst"]))

    async def say(self, moment: Fact, ctx: MatchContext, recent: list[str]) -> Claim:
        """One moment, one call. Raises if the model is unavailable."""
        prompt = f"Moment:\n{_moment_block(moment)}\nPrevious lines: {json.dumps(recent[-3:])}\n{_ONE}"
        text = (await self.agent.run(prompt)).text
        claims = parse_claims('{"claims": [' + text[text.find("{"): text.rfind("}") + 1] + "]}") if "{" in text else []
        return claims[0] if claims else Claim(text="", fact_ids=[], kind=moment.kind)

    async def say_batch(self, moments: list[Fact], ctx: MatchContext, recent: list[str]) -> dict[str, Claim]:
        """Many moments, one call (used for pre-generation to save quota)."""
        prompt = ("Moments, in match order:\n" + "\n".join(_moment_block(m) for m in moments)
                  + f"\nPrevious lines: {json.dumps(recent[-3:])}\n{_MANY}")
        text = (await self.agent.run(prompt)).text
        try:
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except (json.JSONDecodeError, ValueError):
            return {}
        out = {}
        for item in data.get("lines", []):
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                mid = str(item.get("moment", ""))
                out[mid] = Claim(text=item["text"].strip(), fact_ids=[str(i) for i in item.get("fact_ids") or [mid]])
        return out


def _line(moment: Fact, e: Event, claim: Claim, verdict, source: str, blocked: list[str], profile: str) -> Line:
    clock = moment.figures.get("display_clock") or e.display_clock
    return Line(display_clock=str(clock), text=claim.text, fact_ids=claim.fact_ids, evidence=verdict.evidence,
                source=source, kind=moment.kind, blocked=blocked, at_event=e.event_id, period=e.period,
                clock=round(e.clock, 1), profile=profile)


def _finalize(moment: Fact, claim: Claim | None, error: str | None, checker: FactChecker, ctx: MatchContext,
              profile: str, model_mode: bool) -> tuple[Claim, object, str, list[str]]:
    """Check a narrator line; on failure or model error, use the safe template line."""
    if claim is not None:
        verdict = checker.check(claim)
        if verdict.ok:
            claim.source = "model" if model_mode else "template"
            return claim, verdict, claim.source, []
        blocked = verdict.reasons
    else:
        blocked = [f"model unavailable: {error}"]
    safe = Claim(text=template_line(moment, ctx, profile), fact_ids=[moment.id], kind=moment.kind,
                 source="template-fallback")
    return safe, checker.check(safe), "template-fallback", blocked


async def narrate(events: Iterable[Event] | AsyncIterator[Event], ctx: MatchContext,
                  narrator=None) -> AsyncIterator[Line]:
    """Yield checked commentary lines as events arrive (one model call per moment)."""
    narrator = narrator or TemplateNarrator()
    profile = getattr(narrator, "profile", "analyst")
    model_mode = isinstance(narrator, LLMNarrator)
    detector = MomentDetector(ctx)
    recent: list[str] = []

    async def _aiter():
        if hasattr(events, "__aiter__"):
            async for e in events:
                yield e
        else:
            for e in events:
                yield e

    async for e in _aiter():
        for moment in detector.feed(e):
            checker = FactChecker(detector.facts, ctx)
            claim, error = None, None
            try:
                claim = await narrator.say(moment, ctx, recent)
            except Exception as exc:  # noqa: BLE001 - model failure -> safe line
                error = str(exc)
            claim, verdict, source, blocked = _finalize(moment, claim, error, checker, ctx, profile, model_mode)
            if source == "template-fallback":
                log.info("line %s source=template-fallback (%s)", moment.id, "; ".join(blocked))
            recent.append(claim.text)
            yield _line(moment, e, claim, verdict, source, blocked, profile)


async def narrate_all(events: list[Event], ctx: MatchContext, narrator=None, batch_size: int = 12) -> list[Line]:
    """Pre-generation: detect every moment first, then narrate in batches."""
    narrator = narrator or TemplateNarrator()
    profile = getattr(narrator, "profile", "analyst")
    model_mode = isinstance(narrator, LLMNarrator)
    detector = MomentDetector(ctx)
    found: list[tuple[Fact, Event]] = []
    for e in events:
        found += [(m, e) for m in detector.feed(e)]
    checker = FactChecker(detector.facts, ctx)
    lines: list[Line] = []
    recent: list[str] = []
    for i in range(0, len(found), batch_size):
        chunk = found[i: i + batch_size]
        error = None
        try:
            claims = await narrator.say_batch([m for m, _ in chunk], ctx, recent)
        except Exception as exc:  # noqa: BLE001
            claims, error = {}, str(exc)
        for moment, e in chunk:
            claim = claims.get(moment.id)
            err = error or (None if claim else "no line returned for this moment")
            claim, verdict, source, blocked = _finalize(moment, claim, err, checker, ctx, profile, model_mode)
            recent.append(claim.text)
            lines.append(_line(moment, e, claim, verdict, source, blocked, profile))
    return lines
