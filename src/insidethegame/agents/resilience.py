"""Resilient model calls: rate limit, timeout, retry, circuit breaker.

Applied at the level of each single HTTP call to the model (inside Agent
Framework's tool loop), by mixing ResilienceMixin into a chat client class:

    class ResilientOpenAIChatClient(ResilienceMixin, OpenAIChatCompletionClient): ...

GitHub Models free tier, Low tier (gpt-4.1-mini): 15 requests/min, 150/day,
8k input and 4k output tokens per request, 5 concurrent. Defaults stay under
those: 12/min, a daily cap of 140, and a prompt budget of about 7k tokens.

Policy per call:
  0. Refuse prompts estimated above the token budget (callers split or trim
     first; this is the last line of defense) and stop once the daily cap is
     reached. Neither counts as a request.
  1. Wait for the client-side rate limiter (default 12 requests per minute).
  2. Run the call with a timeout (default 15 s). A timeout is not retried,
     because live commentary cannot wait 45 s for one line.
  3. On HTTP 429, any 5xx, or a connection error: retry up to 2 times with
     exponential backoff and jitter, honoring Retry-After when it is short.
     A Retry-After longer than max_retry_after_s (e.g. a daily quota) is not
     waited out.
  4. If the call still fails, raise ModelUnavailable. Callers catch it and
     use the template output instead, logged as source="template-fallback".
  5. After `breaker_threshold` failed calls in a row, the circuit opens and
     every later call fails fast for the rest of the process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("insidethegame.models")


class ModelUnavailable(RuntimeError):
    """The model could not produce a response within policy; use the fallback."""


class InvalidModelResponse(ModelUnavailable):
    """The endpoint answered, but not with a chat completion. Never retried."""


def check_chat_completion_json(data) -> str | None:
    """None if `data` looks like a chat completion, else the reason it does not."""
    if not isinstance(data, dict):
        return f"body is {type(data).__name__}, not a JSON object"
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return "no choices"
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return "choices[0].message missing"
    if isinstance(msg.get("content"), str) or msg.get("tool_calls"):
        return None  # text, or a tool call (content is null then)
    return "choices[0].message has neither content nor tool_calls"


async def _validate_http_response(response) -> None:
    """httpx response hook: a 200 from a chat-completions call must be real chat-completion JSON."""
    if response.request.method != "POST" or not response.request.url.path.endswith("/chat/completions"):
        return
    if response.status_code != 200 or "text/event-stream" in response.headers.get("content-type", ""):
        return  # status errors are classified by the guard; streams are not used here
    await response.aread()
    try:
        data = json.loads(response.content or b"null")
    except ValueError:
        data = None
    reason = check_chat_completion_json(data) if data is not None else "body is not JSON"
    if reason:
        head = (response.content or b"")[:40]
        raise InvalidModelResponse(f"endpoint returned HTTP 200 but not a chat completion ({reason}; "
                                   f"content-type {response.headers.get('content-type', '?')}; body starts {head!r})")


def attach_response_validation(sdk) -> bool:
    """Add the chat-completion check to an OpenAI SDK client's own HTTP client.

    Works whichever HTTP library the installed openai version uses (httpx or its
    successor), because it hooks the client the SDK built. Returns False if the
    client has no hooks; the parsed-response check in ResilienceMixin still applies.
    """
    hooks = getattr(getattr(sdk, "_client", None), "event_hooks", None)
    if not isinstance(hooks, dict):
        return False
    hooks.setdefault("response", []).append(_validate_http_response)
    return True


def check_chat_response(resp) -> None:
    """Agent Framework level check: the parsed response must carry text or a tool call."""
    messages = getattr(resp, "messages", None) or []
    for m in messages:
        if (getattr(m, "text", "") or "").strip():
            return
        if any(getattr(c, "type", None) == "function_call" for c in (getattr(m, "contents", None) or [])):
            return
    raise InvalidModelResponse("model response had no text and no tool call")


@dataclass
class ModelPolicy:
    timeout_s: float = 15.0
    retries: int = 2
    backoff_base_s: float = 1.0
    backoff_max_s: float = 8.0
    max_retry_after_s: float = 20.0
    requests_per_minute: int = 12
    breaker_threshold: int = 3
    daily_cap: int = 140
    max_prompt_tokens: int = 7000

    @classmethod
    def from_env(cls) -> "ModelPolicy":
        return cls(
            timeout_s=float(os.getenv("ITG_MODEL_TIMEOUT", cls.timeout_s)),
            retries=int(os.getenv("ITG_MODEL_RETRIES", cls.retries)),
            requests_per_minute=int(os.getenv("ITG_MODEL_RPM", cls.requests_per_minute)),
            daily_cap=int(os.getenv("ITG_MODEL_DAILY_CAP", cls.daily_cap)),
            max_prompt_tokens=int(os.getenv("ITG_MODEL_MAX_PROMPT_TOKENS", cls.max_prompt_tokens)),
        )


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (about 3.5 characters per token for English)."""
    return math.ceil(len(text) / 3.5)


def messages_tokens(messages) -> int:
    total = 0
    for m in messages or []:
        total += 4 + estimate_tokens(getattr(m, "text", "") or "")
    return total


class DailyUsage:
    """Requests sent today (UTC), persisted so the cap holds across CLI runs."""

    def __init__(self, path: str | Path | None = None, today=None):
        self.path = Path(path) if path else None
        self._today = today or (lambda: datetime.now(timezone.utc).date().isoformat())
        self._mem = {"date": self._today(), "count": 0}

    def _load(self) -> dict:
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                data = {}
        else:
            data = dict(self._mem)
        if data.get("date") != self._today():
            data = {"date": self._today(), "count": 0}
        return data

    def count(self) -> int:
        return int(self._load().get("count", 0))

    def add(self, n: int = 1) -> int:
        data = self._load()
        data["count"] = int(data.get("count", 0)) + n
        self._mem = data
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data))
        return data["count"]

    @classmethod
    def from_env(cls) -> "DailyUsage":
        default = Path.home() / ".cache" / "insidethegame" / "model-usage.json"
        return cls(os.getenv("ITG_USAGE_FILE") or default)


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` starts in any `period_s`."""

    def __init__(self, max_calls: int, period_s: float = 60.0, clock=time.monotonic, sleep=asyncio.sleep):
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        self.max_calls = max_calls
        self.period_s = period_s
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait for a slot. Returns the seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                while self._starts and now - self._starts[0] >= self.period_s:
                    self._starts.popleft()
                if len(self._starts) < self.max_calls:
                    self._starts.append(now)
                    return waited
                delay = self.period_s - (now - self._starts[0])
                waited += delay
                await self._sleep(delay)


def _status_and_retry_after(exc: BaseException) -> tuple[int | None, float | None]:
    """Find an HTTP status (and Retry-After) anywhere in the exception chain."""
    seen = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(status, int):
            headers = getattr(getattr(e, "response", None), "headers", None) or {}
            ra = headers.get("retry-after") if hasattr(headers, "get") else None
            try:
                return status, float(ra) if ra is not None else None
            except ValueError:
                return status, None
        e = e.__cause__ or e.__context__
    return None, None


def _find_in_chain(exc: BaseException, kind: type) -> BaseException | None:
    seen = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        if isinstance(e, kind):
            return e
        seen.add(id(e))
        e = e.__cause__ or e.__context__
    return None


def _is_connection_error(exc: BaseException) -> bool:
    names = set()
    e: BaseException | None = exc
    while e is not None and len(names) < 10:
        names.add(type(e).__name__)
        e = e.__cause__ or e.__context__
    return bool(names & {"APIConnectionError", "ConnectError", "ConnectTimeout", "ReadError", "RemoteProtocolError"})


class ModelGuard:
    """Shared state for one process: limiter, breaker, and call statistics."""

    def __init__(self, policy: ModelPolicy | None = None, limiter: RateLimiter | None = None,
                 sleep=asyncio.sleep, usage: DailyUsage | None = None):
        self.policy = policy or ModelPolicy.from_env()
        self.limiter = limiter or RateLimiter(self.policy.requests_per_minute)
        self.usage = usage or DailyUsage()  # in memory unless a path is given
        self._sleep = sleep
        self.consecutive_failures = 0
        self.circuit_open = False
        self.stats = {"calls": 0, "ok": 0, "retries": 0, "timeouts": 0, "failed": 0, "rate_wait_s": 0.0,
                      "too_large": 0, "capped": 0}

    def check_prompt(self, tokens: int) -> None:
        if tokens > self.policy.max_prompt_tokens:
            self.stats["too_large"] += 1
            raise ModelUnavailable(f"prompt too large: about {tokens} tokens (budget {self.policy.max_prompt_tokens})")

    async def call(self, make_call, prompt_tokens: int = 0):
        """Run make_call() (returns a fresh awaitable each time) under the policy."""
        p = self.policy
        if self.circuit_open:
            raise ModelUnavailable("circuit open after repeated model failures")
        self.check_prompt(prompt_tokens)
        attempt = 0
        while True:
            if p.daily_cap and self.usage.count() >= p.daily_cap:
                self.stats["capped"] += 1
                self.circuit_open = True
                raise ModelUnavailable(f"daily cap of {p.daily_cap} model requests reached")
            self.stats["rate_wait_s"] += await self.limiter.acquire()
            self.usage.add()
            self.stats["calls"] += 1
            try:
                result = await asyncio.wait_for(make_call(), timeout=p.timeout_s)
                self.stats["ok"] += 1
                self.consecutive_failures = 0
                return result
            except asyncio.TimeoutError as e:
                self.stats["timeouts"] += 1
                self._fail()
                raise ModelUnavailable(f"model call timed out after {p.timeout_s:g}s") from e
            except ModelUnavailable:
                self._fail()
                raise
            except Exception as e:  # noqa: BLE001 - classify, then retry or give up
                invalid = _find_in_chain(e, InvalidModelResponse)
                if invalid is not None:
                    self._fail()
                    raise ModelUnavailable(str(invalid)) from e
                status, retry_after = _status_and_retry_after(e)
                retryable = status == 429 or (status is not None and 500 <= status < 600) or _is_connection_error(e)
                if retry_after is not None and retry_after > p.max_retry_after_s:
                    self._fail(open_now=True)
                    raise ModelUnavailable(f"rate limited, retry after {retry_after:g}s (quota)") from e
                if not retryable or attempt >= p.retries:
                    self._fail()
                    label = f"HTTP {status}" if status else type(e).__name__
                    raise ModelUnavailable(f"model call failed: {label}") from e
                attempt += 1
                self.stats["retries"] += 1
                delay = retry_after if retry_after is not None else min(
                    p.backoff_max_s, p.backoff_base_s * 2 ** (attempt - 1)) * (0.8 + 0.4 * random.random())
                log.warning("model call failed (%s), retry %d/%d in %.1fs", status or type(e).__name__,
                            attempt, p.retries, delay)
                await self._sleep(delay)

    def _fail(self, open_now: bool = False) -> None:
        self.stats["failed"] += 1
        self.consecutive_failures += 1
        if open_now or self.consecutive_failures >= self.policy.breaker_threshold:
            if not self.circuit_open:
                log.warning("model circuit opened; using template output for the rest of this run")
            self.circuit_open = True


class ResilienceMixin:
    """Mix into an Agent Framework chat client to guard every model request."""

    guard: ModelGuard

    def _inner_get_response(self, *, messages, options, stream: bool = False, **kwargs):
        inner = super()._inner_get_response  # type: ignore[misc]
        if stream:
            return inner(messages=messages, options=options, stream=True, **kwargs)
        async def validated():
            resp = await inner(messages=messages, options=options, stream=False, **kwargs)
            check_chat_response(resp)
            return resp

        return self.guard.call(validated, prompt_tokens=messages_tokens(messages))
