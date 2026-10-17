# Inside the Game

Explainable, multi-agent match intelligence on synthetic football data. Built for the Microsoft + Premier League *Inside the Game* Developer Hackathon.

> All data here is synthetic. Clubs, players and matches are invented.

## Status

| Part | State |
|---|---|
| Synthetic match generator | Done |
| Agents (analyst, narrator, fact-checker, recap, translator) | Not started |
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

## Tests

```bash
pytest
```

## License

MIT
