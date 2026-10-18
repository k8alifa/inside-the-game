"""Live commentary from a replayed match feed.

    itg-live data/samples/NBA-HCF-7 --speed 60     # 90 minutes in about 90 seconds
    itg-live data/samples/NBA-HCF-7 --speed 0      # instant
    itg-live data/samples/NBA-HCF-7 --jsonl        # one JSON line per comment, for a UI
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from typing import AsyncIterator

from ..sim.schema import Event
from .facts import load_match
from .models import make_chat_client
from .narrator import LLMNarrator, TemplateNarrator, narrate
from .translator import Translator


async def paced(events: list[Event], speed: float) -> AsyncIterator[Event]:
    """Release events on the match clock, scaled by speed (0 = no waiting)."""
    prev = None
    for e in events:
        if speed > 0 and prev is not None and e.period == prev.period:
            await asyncio.sleep(max(0.0, e.clock - prev.clock) / speed)
        prev = e
        yield e


async def _run(args: argparse.Namespace) -> int:
    events, ctx = load_match(args.match_dir)
    client = make_chat_client()
    narrator = LLMNarrator(client, args.profile) if client else TemplateNarrator(args.profile)
    langs = [l for l in args.lang.split(",") if l and l != "en"] if args.lang else []
    if langs and not client:
        print("Translation needs a model (set ITG_MODEL_PROVIDER). Continuing in English.", flush=True)
        langs = []
    translator = Translator(client) if langs else None
    sources: Counter = Counter()
    async for line in narrate(paced(events, args.speed), ctx, narrator):
        sources[line.source] += 1
        translations = await translator.translate(line.text, langs, ctx) if translator else {}
        if args.jsonl:
            print(json.dumps({**line.to_dict(), "translations": {k: v.to_dict() for k, v in translations.items()}},
                             ensure_ascii=False), flush=True)
        else:
            flag = f"  [{line.source}: {'; '.join(line.blocked)}]" if line.blocked else ""
            print(f"[{line.display_clock:>6}] {line.text}{flag}", flush=True)
            for t in translations.values():
                mark = "" if t.ok else f"  [translation blocked: {'; '.join(t.reasons)}]"
                print(f"         {t.lang}: {t.text}{mark}", flush=True)
    if not args.jsonl:
        print(f"\nlines by source: {dict(sources)}", flush=True)
        if client:
            from .models import model_guard
            print(f"model calls: {model_guard().stats}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itg-live", description="Fact-checked live commentary")
    p.add_argument("match_dir")
    p.add_argument("--speed", type=float, default=60.0)
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--lang", default="", help="comma-separated language codes, e.g. ar,es (needs a model)")
    p.add_argument("--profile", default="analyst", choices=["analyst", "casual"])
    args = p.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (KeyboardInterrupt, BrokenPipeError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
