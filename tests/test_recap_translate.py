import asyncio
import json
from pathlib import Path

import pytest

from insidethegame.agents.facts import load_match
from insidethegame.agents.recap import LLMRecapWriter, TemplateRecapWriter, render_markdown, write_recap
from insidethegame.agents.translator import Translator, check_translation
from test_agents import ScriptedChatClient

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "NBA-HCF-7"
GOAL4 = "Kai Underhill scored for Northbridge Athletic at 90+2' from a chance worth 0.35 xG."


@pytest.fixture(scope="module")
def match():
    return load_match(SAMPLE)


# ---------------------------------------------------------------- recap
def test_template_recap_is_ordered_and_verified(match):
    events, ctx = match
    report = asyncio.run(write_recap(events, ctx, TemplateRecapWriter(), n=10))
    assert not report.rejected
    md = render_markdown(report, ctx)
    assert md.startswith("# Northbridge Athletic beat Harbor City FC 3-1")
    goals = [l for l in md.splitlines() if " scored for " in l]
    assert [g.split(" scored")[0] for g in goals] == ["Finn Holloway", "Jonah Haldane", "Cal Fairbank", "Kai Underhill"]
    assert "**Evidence**" in md


def test_llm_recap_revision_keeps_paragraph_order(match):
    events, ctx = match
    round1 = {"claims": [
        {"text": "Northbridge sink Harbor City 3-1", "fact_ids": ["final-score"], "kind": "headline", "order": 0},
        {"text": "Northbridge won 3-1 with 70% of the ball.", "fact_ids": ["final-score"], "kind": "paragraph", "order": 1},
        {"text": "Underhill sealed it at 90+2'.", "fact_ids": ["goal-4"], "kind": "paragraph", "order": 2},
    ]}
    round2 = {"claims": [
        {"text": "Northbridge won 3-1 despite matching Harbor City on xG.", "fact_ids": ["final-score"],
         "kind": "paragraph", "order": 1},
    ]}
    client = ScriptedChatClient([json.dumps(round1), json.dumps(round2)])
    report = asyncio.run(write_recap(events, ctx, LLMRecapWriter(client)))
    md = render_markdown(report, ctx)
    lines = [l for l in md.splitlines() if l and not l.startswith(("---", "**", "_")) and l[0] != "#" and not l[0].isdigit()]
    assert lines[0].startswith("Northbridge won 3-1 despite") and lines[1].startswith("Underhill sealed it")
    assert "70%" not in md
    assert "kind paragraph, order 1" in client.prompts[1]


# ---------------------------------------------------------- translation
@pytest.mark.parametrize("lang, text, ok", [
    ("es", "Kai Underhill marcó para Northbridge Athletic en el 90+2' con una ocasión de 0,35 xG.", True),
    ("es", "Kai Underhill marcó en el 90+3' con una ocasión de 0,35 xG.", False),
    ("es", "El delantero marcó en el 90+2' con una ocasión de 0,35 xG.", False),
    ("es", "Kai Underhill marcó para Northbridge Athletic en el 90+2' con una ocasión de 0,53 xG.", False),
    ("ar", "Kai Underhill Northbridge Athletic 90+2' 0.35 xG.", False),
])
def test_translation_check(match, lang, text, ok):
    _, ctx = match
    assert (check_translation(GOAL4, text, lang, ctx) == []) is ok


def test_translator_falls_back_to_english_when_blocked(match):
    _, ctx = match
    good = "Kai Underhill marcó para Northbridge Athletic en el 90+2' con una ocasión de 0,35 xG."
    bad = "Kai Underhill marcó para Northbridge Athletic en el 90+2' con una ocasión de 0,53 xG."
    reply = json.dumps({"translations": [{"index": 0, "es": good}, {"index": 1, "es": bad}]}, ensure_ascii=False)
    out = asyncio.run(Translator(ScriptedChatClient([reply])).translate_many([GOAL4, GOAL4], ["es"], ctx))
    assert out[0]["es"].ok and out[0]["es"].text.startswith("Kai Underhill marcó")
    assert not out[1]["es"].ok and out[1]["es"].text == GOAL4 and "0.53" in out[1]["es"].reasons[0]
