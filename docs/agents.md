# Analyst and fact-checker

The core idea: **no insight reaches a fan unless the data proves it.**

```
MatchBrief -> [analyst] -> Draft -> [fact_checker] -> Report
                  ^                       |
                  +------- Revision ------+   (rejected claims, max 2 rounds)
```

Built as a Microsoft Agent Framework workflow (`agent_framework.WorkflowBuilder`), with two executors.

## 1. Fact registry (`agents/facts.py`)

Deterministic code computes every fact from the raw events. Each fact has an id, a neutral summary, its figures, the players involved, and the event ids that prove it.

| Kind | Example id | What it says |
|---|---|---|
| `result` | `final-score` | Final score |
| `goal` | `goal-1` | Scorer, assist, minute, xG, running score |
| `team_stats` | `stats-NBA` | Possession, shots, xG, pass accuracy, corners, cards |
| `xg_vs_goals` | `xg-story-HCF` | Scored above or below expectation |
| `momentum_window` | `momentum-30-45` | Share of attacking threat in a 15-minute window |
| `momentum_swing` | `swing-45` | Threat share moved 35+ points between windows |
| `counter_attack` | `counter-2` | Counter that produced a shot: seconds, passes, xG |
| `pressing` | `press-NBA` | High ball wins and how many led to a shot |
| `big_chance_missed` | `big-miss-1` | Shot of 0.30+ xG that did not go in |
| `goalkeeping` | `keeper-HCF` | Saves, xG on target faced, goals prevented |
| `red_card` | `red-1` | Sending off |
| `player_threat` | `top-threat` | Player with the most xG |

## 2. Analyst (`agents/analyst.py`)

Writes insights that cite fact ids. Two backends with one interface:

- `LLMAnalyst`: an Agent Framework `Agent`. Sees the fact catalog, can call `get_fact_details` for figures, returns JSON claims. Model provider is a setting (`agents/models.py`): Foundry, any OpenAI-compatible endpoint, or none.
- `TemplateAnalyst`: offline stand-in with no model. Picks the most important facts, with variety.

## 3. Fact-checker (`agents/checker.py`)

Rebuilds the fact registry itself from the raw events, so it never trusts the analyst. A claim is rejected if:

- it cites no facts, or a fact id that does not exist
- any number in the text (digits, "one" to "twenty", "twice", "brace", "hat-trick"; idioms like "one of" and "no one" are skipped) does not match a cited figure at the precision written ("1.4" matches 1.38; "2" does not match 2.6)
- it names a player, by full name, surname alone or first name alone, who is not in the cited facts (players from other clubs are blocked as not in this match)
- it names a club that did not play, by full or short name ("Elmstead", "Kingsford")

Rejected claims go back to the analyst with reasons for one revision round. Anything still wrong is kept out of the output and listed in the report as blocked.

### Known limits

- The checker verifies numbers and names, not meaning. "Northbridge scored 1" passes if a cited fact contains the figure 1 for another reason. A semantic check is a candidate next step.

## Run it

```bash
itg-analyze data/samples/NBA-HCF-7               # offline template analyst
itg-analyze data/samples/NBA-HCF-7 --json        # full report with evidence ids

# With a model (not yet run against a live endpoint)
pip install -e ".[foundry]"
ITG_MODEL_PROVIDER=foundry FOUNDRY_PROJECT_ENDPOINT=... FOUNDRY_MODEL=... itg-analyze data/samples/NBA-HCF-7
```

# Live narrator

Real-time commentary from a streaming feed (`agents/narrator.py`).

```
event feed -> MomentDetector -> moment fact -> narrator -> fact-checker -> line
```

- **MomentDetector** sees one event at a time and only knows the past. It raises a moment for kickoff, goals (with assist, running score, and counter-attack speed), saves from chances of 0.12+ xG, big chances missed (0.30+ xG), cards, substitutions, momentum swings (checked every 15 minutes), half-time and full-time.
- **Narrator**: `LLMNarrator` (Agent Framework agent, one line of 30 words or fewer per moment) or `TemplateNarrator` (offline).
- **Safety rule for live:** there is no time for a revision round. If the model's line fails the check, the template line, built only from the moment's figures, is used instead. The blocked line and its reasons are logged on the output line.

```bash
itg-live data/samples/NBA-HCF-7 --speed 60     # 90 minutes in about 90 seconds
itg-live data/samples/NBA-HCF-7 --jsonl        # one JSON object per line, for a UI
```

Each output line carries `fact_ids`, `evidence` (event ids), `source` (`model`, `template`, or `template-fallback`) and `blocked` reasons.

# Recap writer

An automated, fact-checked match report (`agents/recap.py`). It reuses the analyst workflow (draft, fact-check, one revision round) with a different writer, so the same checker guards every paragraph. Paragraphs keep their `order` through revisions; any that still fail are dropped. The report ends with an evidence list: fact ids and event ids per paragraph.

```bash
itg-recap data/samples/NBA-HCF-7
```

# Translator

Translates verified English lines and reports (`agents/translator.py`), then checks the translation against its source, because translating can add errors:

- **Numbers:** the set of numbers must be identical, counting repeats. Arabic-Indic digits and decimal commas are normalized first.
- **Names, Latin scripts:** every player and club name in the source must appear, and no other squad name may be added.
- **Names, Arabic:** the translator gets a glossary from the name registry (`agents/names.py`) and must use those spellings. The check finds each one after folding hamza variants, and blocks any squad name the source did not contain. Japanese and Hindi have no map yet, so only numbers are checked there.

A blocked translation is not shown; the English line is used instead. The translator needs a model.

```bash
itg-live  data/samples/NBA-HCF-7 --lang ar,es
itg-recap data/samples/NBA-HCF-7 --lang ar,es
```

# Model calls: resilience and fallback

Every request to the model goes through `agents/resilience.py`: a rate limiter (12/min by default), a 15 s timeout (not retried), two retries with backoff on HTTP 429 or 5xx (a short Retry-After is honored; a long one, such as a daily quota, is not waited out), and a circuit breaker that skips the model after 3 failed calls in a row. Any failure falls back to the template output, which is marked `template-fallback` with the reason.
