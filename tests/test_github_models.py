"""Provider selection, resilience (timeout, retry, breaker, rate limit) and fallbacks."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_framework import BaseChatClient, ChatResponse, Message

from insidethegame.agents import models
from insidethegame.agents.analyst import LLMAnalyst
from insidethegame.agents.facts import load_match
from insidethegame.agents.narrator import LLMNarrator, narrate
from insidethegame.agents.resilience import ModelGuard, ModelPolicy, ModelUnavailable, RateLimiter, ResilienceMixin
from insidethegame.agents.workflow import analyze

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "NBA-HCF-7"


@pytest.fixture(scope="module")
def match():
    return load_match(SAMPLE)


class HTTPError(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(status_code=status,
                                        headers={"retry-after": str(retry_after)} if retry_after else {})


class Recorder:
    def __init__(self):
        self.delays = []

    async def __call__(self, d):
        self.delays.append(d)


def guard(**kw):
    rec = Recorder()
    g = ModelGuard(ModelPolicy(**{"requests_per_minute": 1000, **kw}), sleep=rec)
    return g, rec


def outcomes(*steps):
    """make_call factory: each step is an exception to raise, or a value to return."""
    it = iter(steps)
    calls = []

    def make():
        async def run():
            calls.append(1)
            step = next(it)
            if isinstance(step, BaseException):
                raise step
            if step == "hang":
                await asyncio.sleep(5)
            return step
        return run()
    return make, calls


# ----------------------------------------------------------- provider
@pytest.fixture
def clean_env(monkeypatch):
    for k in ("ITG_MODEL_PROVIDER", "ITG_MODEL", "GITHUB_TOKEN", "OPENAI_API_KEY", "OPENAI_MODEL",
              "OPENAI_BASE_URL", "FOUNDRY_MODEL", "FOUNDRY_PROJECT_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(models, "_GUARD", None)
    return monkeypatch


def test_unset_provider_means_template(clean_env):
    assert models.provider_config().provider is None
    assert models.make_chat_client() is None
    clean_env.setenv("ITG_MODEL_PROVIDER", "template")
    assert models.make_chat_client() is None


def test_github_provider_defaults(clean_env):
    clean_env.setenv("ITG_MODEL_PROVIDER", "github")
    clean_env.setenv("GITHUB_TOKEN", "test-token")
    client = models.make_chat_client()
    assert type(client).__name__ == "ResilientOpenAIChatClient"
    assert isinstance(client, ResilienceMixin)
    assert client.model == "openai/gpt-4.1-mini"
    assert str(client.client.base_url).rstrip("/") == "https://models.github.ai/inference"
    assert client.client.max_retries == 0  # the guard owns retries


def test_github_model_override_and_missing_token(clean_env):
    clean_env.setenv("ITG_MODEL_PROVIDER", "github")
    clean_env.setenv("ITG_MODEL", "openai/gpt-4.1")
    assert models.provider_config().model == "openai/gpt-4.1"
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        models.make_chat_client()


def test_other_providers_resolve_without_network(clean_env):
    clean_env.setenv("ITG_MODEL_PROVIDER", "foundry")
    clean_env.setenv("FOUNDRY_MODEL", "gpt-5-mini")
    assert models.provider_config() == models.ProviderConfig("foundry", "gpt-5-mini")
    clean_env.setenv("ITG_MODEL_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        models.provider_config()


def test_policy_from_env(clean_env):
    clean_env.setenv("ITG_MODEL_TIMEOUT", "7")
    clean_env.setenv("ITG_MODEL_RETRIES", "1")
    clean_env.setenv("ITG_MODEL_RPM", "5")
    p = ModelPolicy.from_env()
    assert (p.timeout_s, p.retries, p.requests_per_minute) == (7.0, 1, 5)


# ----------------------------------------------------------- guard
def test_retries_429_then_succeeds():
    g, rec = guard()
    make, calls = outcomes(HTTPError(429), HTTPError(503), "ok")
    assert asyncio.run(g.call(make)) == "ok"
    assert len(calls) == 3 and g.stats["retries"] == 2 and len(rec.delays) == 2
    assert rec.delays[1] > rec.delays[0] * 1.2  # exponential backoff


def test_gives_up_after_two_retries():
    g, _ = guard()
    make, calls = outcomes(HTTPError(500), HTTPError(502), HTTPError(503), "never")
    with pytest.raises(ModelUnavailable, match="HTTP 503"):
        asyncio.run(g.call(make))
    assert len(calls) == 3


def test_client_errors_are_not_retried():
    g, _ = guard()
    make, calls = outcomes(HTTPError(401), "never")
    with pytest.raises(ModelUnavailable, match="HTTP 401"):
        asyncio.run(g.call(make))
    assert len(calls) == 1


def test_short_retry_after_is_honored_long_one_opens_circuit():
    g, rec = guard()
    make, _ = outcomes(HTTPError(429, retry_after=3), "ok")
    assert asyncio.run(g.call(make)) == "ok" and rec.delays == [3.0]
    g2, _ = guard()
    make, calls = outcomes(HTTPError(429, retry_after=86400), "never")
    with pytest.raises(ModelUnavailable, match="quota"):
        asyncio.run(g2.call(make))
    assert len(calls) == 1 and g2.circuit_open


def test_timeout_is_not_retried():
    g, _ = guard(timeout_s=0.05)
    make, calls = outcomes("hang", "never")
    with pytest.raises(ModelUnavailable, match="timed out"):
        asyncio.run(g.call(make))
    assert len(calls) == 1 and g.stats["timeouts"] == 1


def test_breaker_opens_after_three_failures_then_fails_fast():
    g, _ = guard(retries=0)
    for _ in range(3):
        make, _ = outcomes(HTTPError(500))
        with pytest.raises(ModelUnavailable):
            asyncio.run(g.call(make))
    make, calls = outcomes("never")
    with pytest.raises(ModelUnavailable, match="circuit open"):
        asyncio.run(g.call(make))
    assert calls == []


# ------------------------------------------------------- rate limiter
def test_rate_limiter_caps_requests_per_minute():
    now = [0.0]
    slept = []

    async def fake_sleep(d):
        slept.append(d)
        now[0] += d

    lim = RateLimiter(12, 60.0, clock=lambda: now[0], sleep=fake_sleep)

    async def run():
        for i in range(13):
            await lim.acquire()
            now[0] += 1.0  # one request per second
    asyncio.run(run())
    assert len(slept) == 1 and slept[0] == pytest.approx(48.0)  # 13th waits until the 1st is 60 s old


# ------------------------------------------- end to end through Agent Framework
class _Raw(BaseChatClient):
    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return await self._raw()


class FlakyClient(ResilienceMixin, _Raw):
    """Fails with the given errors, then replies with each script line in turn."""

    def __init__(self, errors, replies, policy=None):
        super().__init__()
        self.errors = list(errors)
        self.replies = list(replies)
        self.calls = 0
        self.guard = ModelGuard(policy or ModelPolicy(requests_per_minute=1000, retries=2), sleep=Recorder())

    async def _raw(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return ChatResponse(messages=[Message(role="assistant", contents=[self.replies.pop(0)])])


def test_narrator_recovers_from_429_through_agent(match):
    events, ctx = match
    good = json.dumps({"text": "And we're under way!", "fact_ids": ["kickoff"]})
    client = FlakyClient([HTTPError(429)], [good])

    async def run():
        return [l async for l in narrate(events[:1], ctx, LLMNarrator(client))]
    lines = asyncio.run(run())
    assert lines[0].source == "model" and lines[0].text == "And we're under way!"
    assert client.calls == 2 and client.guard.stats["retries"] == 1


def test_narrator_falls_back_when_model_is_down(match):
    events, ctx = match
    client = FlakyClient([HTTPError(503)] * 50, [], ModelPolicy(requests_per_minute=1000, retries=2))

    async def run():
        return [l async for l in narrate(events[:500], ctx, LLMNarrator(client))]
    lines = asyncio.run(run())
    assert len(lines) > 3 and all(l.source == "template-fallback" for l in lines)
    assert "model unavailable" in lines[0].blocked[0]
    # 3 moments x (1 try + 2 retries), then the breaker opens and later lines skip the model.
    assert client.guard.circuit_open and client.calls == 9
    assert "circuit open" in lines[-1].blocked[0]


def test_analyst_falls_back_to_template_claims(match):
    events, ctx = match
    client = FlakyClient([HTTPError(500)] * 10, [], ModelPolicy(requests_per_minute=1000, retries=0))
    report = asyncio.run(analyze(events, ctx, LLMAnalyst(client), n_claims=5))
    assert len(report.verified) == 5 and not report.rejected
    assert {v.claim.source for v in report.verified} == {"template-fallback"}
