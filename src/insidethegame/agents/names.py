"""Name registry: every club and player name the checkers need to recognize.

Built from the league in sim/teams.py (read only). For each club it holds the
full name and short name; for each player the full name, first name and
surname. Names are never translated: output languages are English and Spanish,
and both keep names exactly as written.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..sim.teams import TEAMS

@dataclass(frozen=True)
class PlayerName:
    id: str
    team: str
    full: str
    first: str
    last: str


@dataclass
class NameRegistry:
    players: list[PlayerName] = field(default_factory=list)
    clubs: dict[str, tuple[str, ...]] = field(default_factory=dict)  # team id -> aliases, longest first

    @classmethod
    def league(cls) -> "NameRegistry":
        reg = cls()
        for t in TEAMS.values():
            aliases = {t.name, t.short}
            reg.clubs[t.id] = tuple(sorted(aliases, key=len, reverse=True))
            for p in t.players:
                first, last = p.name.split(" ", 1)
                reg.players.append(PlayerName(p.id, t.id, p.name, first, last))
        return reg

    def club_aliases(self, team_id: str) -> tuple[str, ...]:
        return self.clubs.get(team_id, ())


LEAGUE = NameRegistry.league()
