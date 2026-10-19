"""Fictional clubs and rosters.

Every club and player name here is invented. Do not swap in real Premier
League clubs, players, or crests: the hackathon rules forbid unlicensed
trademarks in the demo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

ROLES_433 = ("GK", "DF", "DF", "DF", "DF", "MF", "MF", "MF", "FW", "FW", "FW")
BENCH_ROLES = ("GK", "DF", "MF", "MF", "FW")

_FIRST = (
    "Alex", "Ben", "Cal", "Dario", "Eli", "Finn", "Gabe", "Hugo", "Ivo", "Jonah",
    "Kofi", "Leon", "Mateo", "Nico", "Omar", "Pavel", "Rafi", "Sami", "Theo",
    "Yusuf", "Zane", "Luca", "Adam", "Tariq", "Emre", "Joel", "Kai", "Milo",
)
_LAST = (
    "Ashwell", "Brennick", "Calloway", "Dunmore", "Elstead", "Farrow", "Galbraith", "Holloway",
    "Ingram", "Jessop", "Kirkwood", "Lindqvist", "Marlow", "Northcott", "Okonjo", "Pemberton",
    "Quill", "Radley", "Sutcombe", "Thorne", "Upworth", "Varga", "Whitlock", "Yardley",
    "Zeller", "Abara", "Benhali", "Castellan", "Delmonte", "Esterly", "Fenwick", "Haldane",
    "Amberley", "Blackmore", "Corriden", "Dovecote", "Ebbsworth", "Fairbank", "Gorsuch", "Hartwell",
    "Isleworth", "Jardine", "Kettering", "Langstone", "Merrow", "Nethercott", "Oakhurst", "Pellow",
    "Rosslyn", "Selbourne", "Tamworth", "Underhill", "Vossberg", "Wexley", "Ackroyd", "Bramhall",
    "Cardew", "Dellacourt", "Eskdale", "Fontaine", "Greystoke", "Hambleton", "Ivers", "Kessling",
    "Lowther", "Moorcroft", "Nakagawa", "Ostrander", "Penhallow", "Rinaldi", "Stavros", "Talbeck",
)


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    number: int
    role: str


@dataclass(frozen=True)
class TeamProfile:
    """Style knobs are 0..1. They shape behavior, not outcomes directly."""

    id: str
    name: str
    short: str
    attack: float       # chance quality and finishing
    defense: float      # shot suppression and saves
    press: float        # how hard they press high up the pitch
    tempo: float        # speed of ball circulation
    directness: float   # long, vertical passes vs patient build-up
    technique: float    # ball retention under pressure
    discipline: float   # fewer fouls and cards
    players: tuple[Player, ...]  # 11 starters then 5 bench

    @property
    def starters(self) -> tuple[Player, ...]:
        return self.players[:11]

    @property
    def bench(self) -> tuple[Player, ...]:
        return self.players[11:]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "short": self.short,
            "style": {
                k: getattr(self, k)
                for k in ("attack", "defense", "press", "tempo", "directness", "technique", "discipline")
            },
            "starters": [p.__dict__ for p in self.starters],
            "bench": [p.__dict__ for p in self.bench],
        }


_USED_SURNAMES: set[str] = set()


def _roster(team_id: str, seed: int) -> tuple[Player, ...]:
    """Surnames are unique across the whole league so commentary is unambiguous."""
    rng = random.Random(f"roster:{team_id}:{seed}")
    numbers = [1, 2, 3, 4, 5, 6, 8, 10, 7, 9, 11, 13, 15, 16, 18, 19]
    players = []
    for i, role in enumerate(ROLES_433 + BENCH_ROLES):
        while True:
            last = rng.choice(_LAST)
            if last not in _USED_SURNAMES:
                _USED_SURNAMES.add(last)
                break
        name = f"{rng.choice(_FIRST)} {last}"
        players.append(Player(id=f"{team_id}-{numbers[i]}", name=name, number=numbers[i], role=role))
    return tuple(players)


def _team(team_id, name, short, **style) -> TeamProfile:
    return TeamProfile(id=team_id, name=name, short=short, players=_roster(team_id, 1), **style)


# Four contrasting styles so agents have real tactical differences to explain.
TEAMS: dict[str, TeamProfile] = {
    t.id: t
    for t in (
        _team("NBA", "Northbridge Athletic", "Northbridge",
              attack=0.75, defense=0.60, press=0.85, tempo=0.80, directness=0.35, technique=0.75, discipline=0.55),
        _team("HCF", "Harbor City FC", "Harbor City",
              attack=0.60, defense=0.80, press=0.30, tempo=0.40, directness=0.70, technique=0.55, discipline=0.75),
        _team("ELR", "Elmstead Rovers", "Elmstead",
              attack=0.70, defense=0.55, press=0.55, tempo=0.65, directness=0.50, technique=0.80, discipline=0.60),
        _team("KST", "Kingsford Town", "Kingsford",
              attack=0.50, defense=0.65, press=0.60, tempo=0.55, directness=0.60, technique=0.55, discipline=0.45),
    )
}


# Broadcast colours for the demo overlay (primary, secondary). Invented, and
# deliberately kept out of TeamProfile so match files stay byte-identical.
CLUB_COLORS: dict[str, tuple[str, str]] = {
    "NBA": ("#0f766e", "#f0fdfa"),
    "HCF": ("#1e3a8a", "#f59e0b"),
    "ELR": ("#7f1d1d", "#7dd3fc"),
    "KST": ("#166534", "#fef3c7"),
}
