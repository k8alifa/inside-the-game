"""Event schema for synthetic match data.

Coordinates use a 105 x 68 meter pitch in the *attacking frame* of the team
named on the event: that team always attacks toward x = 105. The goal it
attacks is centered at (105, 34). To compare events from both teams on one
pitch, mirror the other team's points with (105 - x, 68 - y).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
GOAL_WIDTH = 7.32
HALF_LENGTH_S = 45 * 60

EVENT_TYPES = (
    "kickoff",
    "pass",
    "carry",
    "shot",
    "goal",
    "save",
    "tackle",
    "interception",
    "foul",
    "card",
    "free_kick",
    "corner",
    "clearance",
    "substitution",
    "half_end",
)

# Tags carried on events. Possession-level tags are applied to every event
# in the possession once it ends.
TAGS = (
    "counter_attack",   # from the action that completed a fast break, onward
    "high_press_win",   # ball won within ~35 m of the opponent's goal
    "set_piece",        # corner or free kick and the play straight after
    "header",
    "big_chance",       # shot with xG >= 0.30
    "second_yellow",
)


@dataclass
class Event:
    match_id: str
    event_id: int
    period: int
    clock: float              # seconds since the start of this period
    minute: int               # elapsed match minutes (0-based, StatsBomb style)
    display_clock: str        # broadcast style, e.g. "45+2'"
    team: str                 # team id of the acting team
    player: str | None        # player id of the acting player
    type: str
    outcome: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    possession_id: int = 0
    secondary_player: str | None = None  # receiver, fouled player, or player coming on
    xg: float | None = None
    related_event: int | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("x", "y", "end_x", "end_y", "clock"):
            if d[k] is not None:
                d[k] = round(d[k], 1)
        if d["xg"] is not None:
            d["xg"] = round(d["xg"], 3)
        return d
