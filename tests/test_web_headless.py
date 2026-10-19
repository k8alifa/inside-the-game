"""The static overlay in a real browser (skipped when Playwright or Chromium is not available).

Also runs the Node unit tests for web/engine.js when Node is installed.
"""

import functools
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WEB = ROOT / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_engine_node_tests():
    r = subprocess.run(["node", "--test", "web/tests/engine.test.mjs"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.fixture(scope="module")
def server():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    handler = functools.partial(Quiet, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api", reason="Playwright not installed")
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


def open_page(browser, url):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    hosts, errors = set(), []
    page.on("request", lambda r: hosts.add(r.url.split("/")[2]))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function("window.__itg && window.__itg.ready === true", timeout=15000)
    return page, hosts, errors


def test_ticker_shows_a_line_after_play_at_120x(browser, server):
    page, hosts, errors = open_page(browser, server)
    start = page.inner_text(".ticker-text")
    page.click("#speed-group button[data-speed='120']")
    page.click("#btn-play")
    page.wait_for_function(
        "(s) => { const t = document.querySelector('.ticker-text').innerText; return t && t !== s; }",
        arg=start, timeout=15000)
    assert page.inner_text(".ticker-text").strip()
    assert hosts == {server.split("/")[2]}  # only the local server; no external requests
    assert errors == []
    page.close()


def test_compare_view_renders_two_tickers(browser, server):
    page, _, errors = open_page(browser, server + "?view=compare&at=254")
    texts = page.locator(".ticker-text").all_inner_texts()
    assert len(texts) == 2 and all(t.strip() for t in texts) and texts[0] != texts[1]
    assert page.locator(".pane-label").all_inner_texts() == ["Analyst", "Casual fan"]
    assert errors == []
    page.close()


def test_evidence_and_language_controls(browser, server):
    page, _, _ = open_page(browser, server + "?at=1120")
    page.hover(".proof")
    pop = page.inner_text(".proof-pop")
    assert "moment-" in pop and "Match events:" in pop
    assert page.eval_on_selector("#lang-select option[value='es']", "o => o.disabled")  # no verified Spanish
    assert "Spanish needs a bake with a model" in page.get_attribute("#lang-field", "title")
    page.close()


def test_player_card_updates_for_focus_player(browser, server):
    page, _, _ = open_page(browser, server + "?player=NBA-10&at=500")
    first = page.inner_text(".card-grid")
    page.evaluate("window.__itg.seek(5000)")
    page.wait_for_timeout(100)
    assert page.is_visible(".card") and page.inner_text(".card-name") == "Kai Underhill"
    assert page.inner_text(".card-grid") != first
    page.close()
