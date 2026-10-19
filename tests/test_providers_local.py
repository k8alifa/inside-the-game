"""foundry-local selection, success and fallback, and chat-completion validation, against a local HTTP stub."""

import asyncio
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from insidethegame.agents import models
from insidethegame.agents.facts import load_match
from insidethegame.agents.narrator import LLMNarrator, narrate
from insidethegame.agents.resilience import check_chat_completion_json

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "NBA-HCF-7"
GOOD = {"id": "x", "object": "chat.completion", "created": 0, "model": "phi-4-mini",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": json.dumps({"text": "And we're under way!", "fact_ids": ["kickoff"]})}}]}


class Stub:
    """Tiny chat-completions server. mode: 'good' or 'ok' (HTTP 200 with plain 'OK', like a retired endpoint)."""

    def __init__(self):
        self.mode, self.hits = "good", 0
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", 0)))
                stub.hits += 1
                body, ctype = ((json.dumps(GOOD).encode(), "application/json") if stub.mode == "good"
                               else (b"OK", "text/plain"))
                self.send_response(200)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"


@pytest.fixture(scope="module")
def stub():
    s = Stub()
    yield s
    s.srv.shutdown()


@pytest.fixture
def fake_foundry_local(monkeypatch, stub, tmp_path):
    """Stand-in for agent_framework_foundry_local: same base class, manager pointing at the stub."""
    from agent_framework.openai import OpenAIChatCompletionClient

    created = {}

    class FoundryLocalClient(OpenAIChatCompletionClient):
        def __init__(self, model=None, **kw):
            created["model"] = model
            self.manager = types.SimpleNamespace(endpoint=stub.url, api_key="local")
            super().__init__(model=model, api_key="unused", base_url="http://unused.invalid/v1")

    mod = types.ModuleType("agent_framework_foundry_local")
    mod.FoundryLocalClient = FoundryLocalClient
    monkeypatch.setitem(sys.modules, "agent_framework_foundry_local", mod)
    for k in ("ITG_MODEL", "FOUNDRY_LOCAL_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ITG_MODEL_PROVIDER", "foundry-local")
    monkeypatch.setenv("ITG_USAGE_FILE", str(tmp_path / "usage.json"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(models, "_GUARD", None)
    return created


def run_first_line(client):
    events, ctx = load_match(SAMPLE)

    async def go():
        return [l async for l in narrate(events[:1], ctx, LLMNarrator(client))]
    return asyncio.run(go())[0]


def test_foundry_local_selection(fake_foundry_local):
    assert models.provider_config() == models.ProviderConfig("foundry-local", "phi-4-mini")
    client = models.make_chat_client()
    assert type(client).__name__ == "ResilientFoundryLocalClient"
    assert fake_foundry_local["model"] == "phi-4-mini"
    assert client.guard.policy.daily_cap == 0  # local: no daily quota
    assert client.client.max_retries == 0


def test_foundry_local_success_through_validated_client(fake_foundry_local, stub):
    stub.mode = "good"
    line = run_first_line(models.make_chat_client())
    assert line.source == "model" and line.text == "And we're under way!"


def test_plain_ok_body_is_rejected_without_retry(fake_foundry_local, stub):
    stub.mode, before = "ok", stub.hits
    client = models.make_chat_client()
    line = run_first_line(client)
    assert line.source == "template-fallback"
    assert "not a chat completion" in line.blocked[0] and "b'OK'" in line.blocked[0]
    assert stub.hits - before == 1 and client.guard.stats["retries"] == 0


def test_foundry_local_missing_package_falls_back_to_template(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "agent_framework_foundry_local", None)  # import fails
    monkeypatch.setenv("ITG_MODEL_PROVIDER", "foundry-local")
    monkeypatch.setattr(models, "_GUARD", None)
    msgs = []
    assert models.client_or_template(log=msgs.append) is None
    assert 'pip install -e ".[local]"' in msgs[0]


@pytest.mark.parametrize("data, ok", [
    (GOOD, True),
    ({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "t"}]}}]}, True),
    ("OK", False),
    ({}, False),
    ({"choices": []}, False),
    ({"choices": [{"message": {"role": "assistant", "content": None}}]}, False),
])
def test_chat_completion_shape_check(data, ok):
    assert (check_chat_completion_json(data) is None) is ok
