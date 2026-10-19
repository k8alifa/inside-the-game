"""Run the analyst and fact-checker on a match folder.

    itg-analyze data/samples/NBA-HCF-7
    itg-analyze data/samples/NBA-HCF-7 --json > report.json
    ITG_MODEL_PROVIDER=foundry itg-analyze data/samples/NBA-HCF-7
"""

from __future__ import annotations

import argparse
import asyncio
import json

from .analyst import LLMAnalyst, TemplateAnalyst
from .facts import load_match
from .models import client_or_template
from .workflow import analyze


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itg-analyze", description="Explainable match insights, fact-checked")
    p.add_argument("match_dir", help="folder with events.jsonl and match.json")
    p.add_argument("--claims", type=int, default=8)
    p.add_argument("--rounds", type=int, default=2, help="max analyst/checker rounds")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = p.parse_args(argv)

    events, ctx = load_match(args.match_dir)
    client = client_or_template()
    backend = LLMAnalyst(client) if client else TemplateAnalyst()
    report = asyncio.run(analyze(events, ctx, backend, n_claims=args.claims, max_rounds=args.rounds))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0
    mode = "model" if client else "offline template (set ITG_MODEL_PROVIDER to use a model)"
    print(f"{ctx.team_names[ctx.home]} vs {ctx.team_names[ctx.away]}  |  analyst: {mode}  |  rounds: {report.rounds}\n")
    for i, v in enumerate(report.verified, 1):
        ev = ", ".join(map(str, v.evidence[:8])) + (" ..." if len(v.evidence) > 8 else "")
        print(f"{i}. {v.claim.text}\n   facts: {', '.join(v.claim.fact_ids)}  |  events: {ev or 'all events'}\n")
    if report.rejected:
        print("Blocked by the fact-checker:")
        for v in report.rejected:
            print(f"  x {v.claim.text}\n    {'; '.join(v.reasons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
