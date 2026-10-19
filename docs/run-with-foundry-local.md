# Run with Microsoft Foundry Local ($0)

Foundry Local runs a small model on your own computer: no Azure account, no card, no daily quota. These steps do the live smoke checks and bake the live demo bundle. Run them on your computer. The cloud sandbox can't run Foundry Local.

## 1. Install Foundry Local

Windows (PowerShell):

```powershell
winget install Microsoft.FoundryLocal
```

macOS (Terminal):

```bash
brew install microsoft/foundrylocal/foundrylocal
```

> If that tap name fails, use the macOS install step in Microsoft's Foundry Local docs.

Check it, then download and start the default model once (the first run is slow while it downloads):

```bash
foundry service status
foundry model run phi-4-mini
```

Type a test question, then exit the chat (Ctrl+C). The model stays cached. `foundry model list` shows other models; any alias from that list works as `ITG_MODEL`.

## 2. Set up the project

macOS / Linux:

```bash
git clone https://github.com/k8alifa/inside-the-game && cd inside-the-game
git checkout feat/fan-overlay
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,local]"
export ITG_MODEL_PROVIDER=foundry-local ITG_MODEL=phi-4-mini
```

Windows (PowerShell):

```powershell
git clone https://github.com/k8alifa/inside-the-game; cd inside-the-game
git checkout feat/fan-overlay
py -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[dev,local]"
$env:ITG_MODEL_PROVIDER = "foundry-local"; $env:ITG_MODEL = "phi-4-mini"
```

## 3. Smoke checks (a few minutes)

```bash
itg-analyze data/samples/NBA-HCF-7 --claims 2 --rounds 1
itg-live data/samples/NBA-HCF-7 --speed 0 --profile casual --lang en,es --limit 6
itg-live data/samples/NBA-HCF-7 --speed 0 --profile analyst --lang en,es --limit 6
itg-recap data/samples/NBA-HCF-7 --profile casual --lang en,es
itg-recap data/samples/NBA-HCF-7 --profile analyst --lang en,es
```

What to look for:

- `lines by source` includes `model`. Some `template-fallback` lines are normal: those are model lines the fact-checker blocked, and the reason is printed next to them.
- Spanish lines appear under each English line. Any marked `translation blocked` failed the number or name check and are not used.
- If you see `Model unavailable: ...`, the run continued in template mode. Copy the message; it names what is missing.

## 4. Bake the live demo bundle

```bash
itg-bake --match data/samples/NBA-HCF-7 --profiles analyst,casual --langs en,es --web
```

This writes `data/baked/NBA-HCF-7/` and `web/data/NBA-HCF-7/`. Check `data/baked/NBA-HCF-7/manifest.json`:

- `"provider": "foundry-local"` and the model name
- `"langs"`: `"es"` should be `"verified"` or `"partial"`
- `"model_calls"` shows how many requests were made

The offline copy in `data/baked/NBA-HCF-7.template/` stays as it is, so the demo always works without a model.

## 5. Look at it

```bash
python -m http.server -d web 8000
```

Open http://localhost:8000/ and http://localhost:8000/?view=compare. With a live bundle, Spanish is selectable.

## 6. Hand it back

Either commit it on a new branch:

```bash
git checkout -b feat/live-bake
git add data/baked/NBA-HCF-7 web/data
git commit -m "Live bake with Foundry Local (phi-4-mini)"
git push -u origin feat/live-bake
```

or zip `data/baked/NBA-HCF-7` and `web/data` and send them over. Never commit `.env` or `~/.cache/insidethegame/`.

After a live bake, `pytest -q` still passes: the test that compares the committed web bundle with a template bake skips itself when the bundle's provider is not `template`.
