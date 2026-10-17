"""Synthetic, football-realistic match data generator."""

from .engine import GENERATOR_VERSION, MatchResult, MatchSimulator, xg_model
from .io import load_events, write_match
from .schema import Event
from .stats import team_stats
from .teams import TEAMS, Player, TeamProfile

__all__ = [
    "GENERATOR_VERSION",
    "Event",
    "MatchResult",
    "MatchSimulator",
    "Player",
    "TEAMS",
    "TeamProfile",
    "load_events",
    "team_stats",
    "write_match",
    "xg_model",
]
