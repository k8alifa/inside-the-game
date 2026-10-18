"""Write a fact-checked match report.

    itg-recap data/samples/NBA-HCF-7
    itg-recap data/samples/NBA-HCF-7 --lang ar,es > report.md   (translation needs a model)
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from .facts import load_match
from .models import make_chat_client
from .recap import LLMRecapWriter, TemplateRecapWriter, render_markdown, write_recap
from .translator import LANGUAGES, Translator


async def _run(args: argparse.Namespace) -> int:
    events, ctx = load_match(args.match_dir)
    client = make_chat_client()
    writer = LLMRecapWriter(client, args.profile) if client else TemplateRecapWriter(args.profile)
    report = await write_recap(events, ctx, writer, n=args.paragraphs + 1, max_rounds=args.rounds)
    md = render_markdown(report, ctx)
    print(md)
    sources = Counter(v.claim.source for v in report.verified)
    print(f"_Sources: {dict(sources)}; blocked by checker: {len(report.rejected)}_")
    langs = [l for l in args.lang.split(",") if l and l != "en"]
    if langs and not client:
        print("_Translation needs a model (set ITG_MODEL_PROVIDER)._")
        return 0
    if langs:
        items = sorted(report.verified, key=lambda v: v.claim.order if v.claim.order is not None else 999)
        rows = await Translator(client).translate_many([v.claim.text for v in items], langs, ctx)
        for lang in langs:
            print(f"\n---\n## {LANGUAGES.get(lang, (lang,))[0]}\n")
            ok = 0
            for row in rows:
                t = row[lang]
                ok += t.ok
                print(t.text if t.ok else f"{t.text}  _(translation blocked: {'; '.join(t.reasons)})_")
                print()
            print(f"_{lang}: {ok}/{len(rows)} paragraphs translated and verified_")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itg-recap", description="Fact-checked match report")
    p.add_argument("match_dir")
    p.add_argument("--paragraphs", type=int, default=8)
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--lang", default="")
    p.add_argument("--profile", default="analyst", choices=["analyst", "casual"])
    return asyncio.run(_run(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
