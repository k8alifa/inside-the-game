"""Write the static web bundle the overlay reads (web/data/<match_id>/).

Files:
  manifest.json                 teams, lineups, languages, profiles, focus players, provider
  timeline.json                 one row per event: clock, type, team, player, score after
  stats.json                    running team stats per match minute, for the analyst strip
  facts.json                    every fact an item cites (moment facts and player-card facts)
  overlay.<profile>.json        OverlayItems for each profile
  overlay.<profile>.<club>.json the same, framed for a favourite club
  players/<player_id>.json      a focus player's items and running card
  recap.<profile>.json          the verified match report (shown at full time)

Everything is derived from the events plus already-verified lines, so the
page needs no server and no model.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..agents.facts import MatchContext, _momentum, load_match
from ..agents.narrator import MomentDetector
from ..sim.stats import team_stats
from ..sim.teams import CLUB_COLORS, TEAMS
from .overlay import (MODES, SCHEMA_VERSION, apply_favourite, items_from_narration, make_item, verify_items)
from .players import build_card, card_text, focus_players

DISCLAIMER = "Synthetic data. All clubs, players and events are invented."


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def timeline_rows(events, ctx: MatchContext) -> list[dict]:
    score = {ctx.home: 0, ctx.away: 0}
    rows = []
    for e in events:
        if e.type == "goal":
            score[e.team] += 1
        rows.append({"event_id": e.event_id, "period": e.period, "clock": round(e.clock, 1),
                     "display_clock": e.display_clock, "type": e.type, "team": e.team, "player": e.player,
                     "score": [score[ctx.home], score[ctx.away]]})
    return rows


def stats_snapshots(events, ctx: MatchContext) -> list[dict]:
    """Running team stats at the last event of each match minute (and at each half's end)."""
    marks = []
    for i, e in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        if nxt is None or (nxt.period, nxt.minute) != (e.period, e.minute) or e.type == "half_end":
            marks.append(i)
    snaps = []
    for i in marks:
        prefix = events[: i + 1]
        s = team_stats(prefix, (ctx.home, ctx.away))
        windows = _momentum(prefix, ctx) if any(x.type in ("pass", "carry") for x in prefix) else []
        w = windows[-1] if windows else None
        snaps.append({
            "at_event": events[i].event_id,
            "teams": {t: {k: s[t][k] for k in ("possession_pct", "shots", "shots_on_target", "xg")}
                      for t in (ctx.home, ctx.away)},
            "momentum": {"window": [w["start"], w["end"]], "share": w["share"]} if w else None,
        })
    return snaps


def _lineup(team: dict) -> dict:
    return {"starters": [{k: p[k] for k in ("id", "name", "number", "role")} for p in team["starters"]],
            "bench": [{k: p[k] for k in ("id", "name", "number", "role")} for p in team["bench"]]}


def write_web_bundle(match_dir: str | Path, out_root: str | Path, narration: dict[str, list[dict]],
                     recaps: dict[str, dict], info: dict) -> Path:
    """narration: {profile: [narration rows from itg-bake]}; recaps: {profile: recap json}; info: provider etc."""
    events, ctx = load_match(match_dir)
    summary = json.loads((Path(match_dir) / "match.json").read_text())
    out = Path(out_root) / ctx.match_id
    if out.exists():
        shutil.rmtree(out)  # deterministic: no stale files from an older bake
    langs_status = info["langs"]
    langs = [l for l in langs_status if l != "en"]

    detector = MomentDetector(ctx)
    for e in events:
        detector.feed(e)
    facts = dict(detector.facts)

    roles = {p["id"]: p["role"] for t in (summary["home"], summary["away"]) for p in t["starters"] + t["bench"]}
    focus = focus_players(events, ctx, roles)
    by_id = {e.event_id: e for e in events}

    overlays: dict[str, list[dict]] = {}
    for profile in MODES:
        items = items_from_narration(narration.get(profile, []), facts, ctx, profile, langs)
        overlays[profile] = items
        _dump(out / f"overlay.{profile}.json", {"schema_version": SCHEMA_VERSION, "match_id": ctx.match_id,
                                                "profile": profile, "favourite": None, "items": items})
        for club in (ctx.home, ctx.away):
            fav = apply_favourite(items, facts, club)
            _dump(out / f"overlay.{profile}.{club}.json", {"schema_version": SCHEMA_VERSION, "match_id": ctx.match_id,
                                                           "profile": profile, "favourite": club, "items": fav})

    players_meta = []
    for pid in focus:
        card = build_card(events, ctx, pid)
        facts.update(card.facts)
        keeper = roles.get(pid) == "GK"
        per_profile = {}
        for profile in MODES:
            mine = [it for it in overlays[profile] if any(pid in facts[f].players for f in it["fact_ids"] if f in facts)]
            cards = []
            for (eid, reason), (fid, fact) in zip(card.moments, card.facts.items()):
                e = by_id[eid]
                text = card_text(fact, ctx, profile, keeper)
                cards.append(make_item(match_id=ctx.match_id, profile=profile, at_event=eid, period=e.period,
                                       clock=round(e.clock, 1), display_clock=e.display_clock, kind="player_card",
                                       text=text, translations={l: {"text": text, "ok": False} for l in langs},
                                       fact_ids=[fid], evidence=fact.evidence, source="template", blocked=[],
                                       favourite=pid))
            per_profile[profile] = sorted(mine + cards, key=lambda i: (i["period"], i["clock"], i["at_event"]))
        team = pid.split("-")[0]
        meta = {"id": pid, "name": ctx.player_names[pid], "team": team, "role": roles.get(pid)}
        players_meta.append(meta)
        _dump(out / "players" / f"{pid}.json", {"schema_version": SCHEMA_VERSION, "match_id": ctx.match_id,
                                                "player": meta, "card": card.snapshots, "items": per_profile})

    problems = []
    for profile in MODES:
        problems += verify_items(overlays[profile], facts, ctx)
    if problems:
        raise ValueError("overlay items failed the FactChecker: " + "; ".join(problems[:5]))

    _dump(out / "timeline.json", {"match_id": ctx.match_id, "events": timeline_rows(events, ctx)})
    _dump(out / "stats.json", {"match_id": ctx.match_id, "snapshots": stats_snapshots(events, ctx)})
    _dump(out / "facts.json", {k: f.to_dict() for k, f in sorted(facts.items())})
    for profile, recap in recaps.items():
        _dump(out / f"recap.{profile}.json", recap)

    teams = []
    for side in ("home", "away"):
        t = summary[side]
        primary, secondary = CLUB_COLORS.get(t["id"], ("#334155", "#e2e8f0"))
        teams.append({"id": t["id"], "name": t["name"], "short": TEAMS[t["id"]].short if t["id"] in TEAMS else t["short"],
                      "side": side, "colors": {"primary": primary, "secondary": secondary}, "lineup": _lineup(t)})
    last = events[-1]
    manifest = {
        "schema_version": SCHEMA_VERSION, "match_id": ctx.match_id, "teams": teams,
        "final_score": summary["score"], "duration": {"periods": 2, "last_event": last.event_id},
        "langs": langs_status, "profiles": list(MODES),
        "favourites": [ctx.home, ctx.away], "focus_players": players_meta,
        "provider": info.get("provider"), "model": info.get("model"), "generated_at": info.get("generated_at"),
        "disclaimer": DISCLAIMER,
        "files": sorted(p.relative_to(out).as_posix() for p in out.rglob("*.json")) + ["manifest.json"],
    }
    _dump(out / "manifest.json", manifest)
    index_path = Path(out_root) / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"matches": []}
    if ctx.match_id not in index["matches"]:
        index["matches"] = sorted(index["matches"] + [ctx.match_id])
    index["default"] = index.get("default") or ctx.match_id
    _dump(index_path, index)
    return out
