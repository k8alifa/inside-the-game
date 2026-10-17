"""Write a fact-checked match report.

    itg-recap data/samples/NBA-HCF-7
    itg-recap data/samples/NBA-HCF-7 --lang ar,es > report.md   (translation needs a model)
"""

from __future__ import annotations

import argparse
import asyncio

from .facts import load_match
from .models import make_chat_client
from .recap import LLMRecapWriter, TemplateRecapWriter, render_markdown, write_recap
from .translator import LANGUAGES, Translator


async def _run(args: argparse.Namespace) -> int:
    events, ctx = load_match(args.match_dir)
    client = make_chat_client()
    writer = LLMRecapWriter(client) if client else TemplateRecapWriter()
    report = await write_recap(events, ctx, writer, n=args.paragraphs + 1, max_rounds=args.rounds)
    md = render_markdown(report, ctx)
    print(md)
    langs = [l for l in args.lang.split(",") if l]
    if langs and not client:
        print("_Translation needs a model (set ITG_MODEL_PROVIDER)._")
        return 0
    if langs:
        translator = Translator(client)
        body = md.split("---")[0].strip()
        result = await translator.translate(body, langs, ctx)
        for lang, t in result.items():
            name = LANGUAGES.get(lang, (lang,))[0]
            print(f"\n---\n## {name}\n")
            print(t.text if t.ok else f"_Translation blocked ({'; '.join(t.reasons)}). English shown above._")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itg-recap", description="Fact-checked match report")
    p.add_argument("match_dir")
    p.add_argument("--paragraphs", type=int, default=8)
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--lang", default="")
    return asyncio.run(_run(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
