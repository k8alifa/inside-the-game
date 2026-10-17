# Inside the Game

Explainable, multi-agent match intelligence on synthetic football data. Built for the Microsoft + Premier League *Inside the Game* Developer Hackathon.

> All data here is synthetic. Clubs, players and matches are invented.

## Status

| Part | State |
|---|---|
| Synthetic match generator | Done |
| Analyst + fact-checker (Agent Framework workflow) | Done, offline. Live model not wired yet |
| Live narrator (fact-checked, safe fallback) | Done, offline |
| Recap and translator agents | Not started |
| Orchestration and hosting | Not started |

## Synthetic match generator

A seeded, possession-based simulator. Four fictional clubs with different styles (pressing, direct, possession, physical) so the agents have real tactical differences to explain. Same seed, same match.

```bash
pip install -e ".[dev]"

itg-sim teams                                   # list clubs
itg-sim generate --home NBA --away HCF --seed 7 # writes data/matches/NBA-HCF-7/
itg-sim generate --count 20                     # 20 matches, seeds 7..26
itg-sim replay data/matches/NBA-HCF-7/events.jsonl --speed 60   # live feed, 90 min in ~90 s
```

Without installing: `PYTHONPATH=src python -m insidethegame.sim ...`

A sample match is in `data/samples/NBA-HCF-7/`. The event format is in [docs/event-schema.md](docs/event-schema.md).

## Analyst and fact-checker

Every insight cites facts computed from the events. A separate fact-checker rebuilds those facts itself and blocks any claim whose numbers or names the data does not support. See [docs/agents.md](docs/agents.md).

```bash
itg-analyze data/samples/NBA-HCF-7          # post-match insights
itg-live data/samples/NBA-HCF-7 --speed 60  # live commentary on a replayed feed
```

## Tests

```bash
pytest
```

## License

MIT
