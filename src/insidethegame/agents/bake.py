"""Pre-generate every demo output once, so the public demo makes zero model calls.

    itg-bake --match data/samples/NBA-HCF-7 --profiles analyst,casual --langs en,es \\
             --out data/baked/NBA-HCF-7/

Writes, under --out:

    manifest.json              what was baked, by which provider and model, and counts
    facts.json                 the fact registry (evidence for every line)
    narration.<profile>.json   live lines with replay timing, sources, translations
    recap.<profile>.json       headline and paragraphs with evidence and translations

Model calls are batched (narration and translation) to fit free-tier quotas.
Every line passes the fact-checker; anything the model gets wrong, or any
model outage, is replaced by the template line and marked template-fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .facts import build_facts, load_match
from .models import make_chat_client, model_guard, provider_config
from .narrator import LLMNarrator, TemplateNarrator, narrate_all
from .recap import LLMRecapWriter, TemplateRecapWriter, write_recap
from .translator import Translator, parse_langs

SCHEMA_VERSION = 1


def _status(t: dict) -> str:
    if t["ok"]:
        return "ok"
    unavailable = any(r.startswith(("no model", "model unavailable")) for r in t["reasons"])
    return "unavailable" if unavailable else "blocked"


def _no_model(texts: list[str], langs: list[str]) -> list[dict]:
    return [{l: {"lang": l, "text": t, "ok": False, "reasons": ["no model configured"]} for l in langs} for t in texts]


async def _translate(translator, texts: list[str], langs: list[str], ctx) -> list[dict]:
    if not langs:
        return [{} for _ in texts]
    if translator is None:
        return _no_model(texts, langs)
    rows = await translator.translate_many(texts, langs, ctx)  # batched by size inside
    return [{l: t.to_dict() for l, t in row.items()} for row in rows]


async def bake(match_dir: str | Path, out_dir: str | Path, profiles: list[str], langs: list[str],
               client=None, recap_paragraphs: int = 8) -> dict:
    events, ctx = load_match(match_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    targets = parse_langs(",".join(langs))
    translator = Translator(client) if client else None
    sources: Counter = Counter()
    translations: Counter = Counter()

    facts = build_facts(events, ctx)
    (out / "facts.json").write_text(json.dumps({k: f.to_dict() for k, f in facts.items()}, indent=1))

    files = {}
    for profile in profiles:
        narrator = LLMNarrator(client, profile) if client else TemplateNarrator(profile)
        lines = await narrate_all(events, ctx, narrator)
        trans = await _translate(translator, [l.text for l in lines], targets, ctx)
        rows = []
        for line, tr in zip(lines, trans):
            sources[line.source] += 1
            translations.update(_status(t) for t in tr.values())
            rows.append({**line.to_dict(), "translations": tr})
        name = f"narration.{profile}.json"
        (out / name).write_text(json.dumps({"schema_version": SCHEMA_VERSION, "match_id": ctx.match_id,
                                            "profile": profile, "lines": rows}, indent=1, ensure_ascii=False))
        files[name] = len(rows)

        writer = LLMRecapWriter(client, profile) if client else TemplateRecapWriter(profile)
        report = await write_recap(events, ctx, writer, n=recap_paragraphs + 1)
        items = sorted(report.verified, key=lambda v: v.claim.order if v.claim.order is not None else 999)
        trans = await _translate(translator, [v.claim.text for v in items], targets, ctx)
        recap_rows = []
        for v, tr in zip(items, trans):
            sources[v.claim.source] += 1
            translations.update(_status(t) for t in tr.values())
            recap_rows.append({**v.to_dict(), "translations": tr})
        headline = next((r for r in recap_rows if r["kind"] == "headline"), None)
        name = f"recap.{profile}.json"
        (out / name).write_text(json.dumps({
            "schema_version": SCHEMA_VERSION, "match_id": ctx.match_id, "profile": profile,
            "headline": headline, "paragraphs": [r for r in recap_rows if r is not headline],
            "blocked": [v.to_dict() for v in report.rejected], "rounds": report.rounds,
        }, indent=1, ensure_ascii=False))
        files[name] = len(recap_rows)

    cfg = provider_config()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "match_id": ctx.match_id,
        "teams": ctx.team_names,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": cfg.provider if client else "template",
        "model": cfg.model if client else None,
        "profiles": profiles,
        "langs": ["en", *targets],
        "files": files,
        "counts": {"sources": dict(sources), "translations": dict(translations)},
        "model_calls": dict(model_guard().stats) if client else {},
        "disclaimer": "Synthetic data. All clubs, players and events are invented.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itg-bake", description="Pre-generate verified demo outputs")
    p.add_argument("--match", required=True, help="match folder with events.jsonl and match.json")
    p.add_argument("--profiles", default="analyst,casual")
    p.add_argument("--langs", default="en", help="en and/or es")
    p.add_argument("--out", required=True)
    p.add_argument("--paragraphs", type=int, default=8)
    args = p.parse_args(argv)
    try:
        parse_langs(args.langs)
    except ValueError as e:
        p.error(str(e))
    client = make_chat_client()
    m = asyncio.run(bake(args.match, args.out, [x for x in args.profiles.split(",") if x],
                         [x for x in args.langs.split(",") if x], client, args.paragraphs))
    print(f"Baked {m['match_id']} with {m['provider']}{' / ' + m['model'] if m['model'] else ''} -> {args.out}")
    print(f"  lines by source: {m['counts']['sources']}")
    if m["counts"]["translations"]:
        print(f"  translations: {m['counts']['translations']}")
    if m["model_calls"]:
        print(f"  model calls: {m['model_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
