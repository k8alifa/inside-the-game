# Inside the Game

Explainable, multi-agent match intelligence on synthetic football data. Built for the Microsoft + Premier League *Inside the Game* Developer Hackathon.

> All data here is synthetic. Clubs, players and matches are invented.

## Status

| Part | State |
|---|---|
| Synthetic match generator | Done |
| Analyst + fact-checker (Agent Framework workflow) | Done, offline. Live model not wired yet |
| Live narrator (fact-checked, safe fallback) | Done, offline |
| Recap writer (fact-checked) | Done, offline |
| Translator, English to Spanish (numbers and names re-checked) | Done; needs a model to run |
| GitHub Models provider + resilience | Done; mocked tests only, no live run yet |
| Pre-generated demo bundle (`itg-bake`) | Done; current bundle is template mode |
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

**Optional calibration.** `itg-sim generate --calibration data/calibration/simconfig_calibrated.json` uses constants fitted to real top-division result aggregates (goals, home/draw/away rates) from a CC0 source. Only aggregates are stored; clubs, players and events stay invented. Without the flag the output is unchanged. See [docs/calibration.md](docs/calibration.md); a calibrated sample is in `data/samples/calibrated-NBA-HCF-7/`.

## Analyst and fact-checker

Every insight cites facts computed from the events. A separate fact-checker rebuilds those facts itself and blocks any claim whose numbers or names the data does not support. See [docs/agents.md](docs/agents.md).

```bash
itg-analyze data/samples/NBA-HCF-7          # post-match insights
itg-live data/samples/NBA-HCF-7 --speed 60  # live commentary on a replayed feed
itg-recap data/samples/NBA-HCF-7            # match report
itg-recap data/samples/NBA-HCF-7 --lang es   # with a Spanish version (needs a model)
```

## Connect a model ($0)

| `ITG_MODEL_PROVIDER` | What it uses | Cost | Needs | Status |
|---|---|---|---|---|
| *(unset)* | Offline templates | $0 | nothing | Tested |
| `github` | GitHub Models, `openai/gpt-4.1-mini` by default (`ITG_MODEL` to change) | $0, free tier with rate limits | `GITHUB_TOKEN` with Models: read | Tested with a mocked model; no live run yet |
| `openai` | Any OpenAI-compatible endpoint | depends | `OPENAI_API_KEY`, `ITG_MODEL`, optional `OPENAI_BASE_URL` | Untested live |
| `foundry` | Microsoft Foundry project | Azure billing | `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL` | Untested live |

**$0 setup with GitHub Models**

1. On GitHub: Settings › Developer settings › Fine-grained tokens › Generate new token. Under Account permissions, set **Models** to **Read**. No repository access is needed.
2. Set the environment (see `.env.example`; never commit the token):
   ```bash
   pip install -e ".[openai]"
   export ITG_MODEL_PROVIDER=github GITHUB_TOKEN=<your token>
   ```
3. Run it:
   ```bash
   itg-live data/samples/NBA-HCF-7 --speed 0
   itg-recap data/samples/NBA-HCF-7 --lang es
   ```

**Resilience:** every model request is rate limited (`ITG_MODEL_RPM`, default 12/min), has a timeout (`ITG_MODEL_TIMEOUT`, default 15 s), and is retried twice with backoff on HTTP 429 or 5xx. Prompts are kept under about 7k tokens (`ITG_MODEL_MAX_PROMPT_TOKENS`) by splitting batches and trimming the fact list. A daily counter stops model calls at 140 (`ITG_MODEL_DAILY_CAP`, counted in `ITG_USAGE_FILE`, default `~/.cache/insidethegame/model-usage.json`, reset at midnight UTC). If anything fails, the template line is used and marked `template-fallback`. After 3 failed calls in a row the model is skipped for the rest of the run.

GitHub Models free tier for `gpt-4.1-mini` (Low tier): 15 requests/min, 150/day, 8k input and 4k output tokens per request, 5 concurrent.

**Languages:** English (primary) and Spanish (`--lang es`). Names are never translated.

## Pre-generated demo

The public demo makes no live model calls. `itg-bake` runs the full pipeline once and saves verified outputs:

```bash
itg-bake --match data/samples/NBA-HCF-7 --profiles analyst,casual --langs en,es --out data/baked/NBA-HCF-7/
```

| File | Contents |
|---|---|
| `manifest.json` | Provider, model, profiles, languages, counts by source, model-call stats |
| `facts.json` | The fact registry: evidence behind every line |
| `narration.<profile>.json` | Live lines with replay timing (`period`, `clock`, `at_event`), source, evidence, translations |
| `recap.<profile>.json` | Headline and paragraphs with evidence and translations, plus blocked drafts |

Narration and translation are batched, so a two-profile, two-language bake takes about 12 model calls with no revisions (more if the model calls tools or the checker sends drafts back). The copy in `data/baked/NBA-HCF-7/` was baked in template mode; rebake with a model before recording the demo.

## Tests

```bash
pytest
```

## License

MIT
