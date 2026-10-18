"""Resilient model calls: rate limit, timeout, retry, circuit breaker.

Applied at the level of each single HTTP call to the model (inside Agent
Framework's tool loop), by mixing ResilienceMixin into a chat client class:

    class ResilientOpenAIChatClient(ResilienceMixin, OpenAIChatCompletionClient): ...

Policy per call:
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
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("insidethegame.models")


class ModelUnavailable(RuntimeError):
    """The model could not produce a response within policy; use the fallback."""


@dataclass
class ModelPolicy:
    timeout_s: float = 15.0
    retries: int = 2
    backoff_base_s: float = 1.0
    backoff_max_s: float = 8.0
    max_retry_after_s: float = 20.0
    requests_per_minute: int = 12
    breaker_threshold: int = 3

    @classmethod
    def from_env(cls) -> "ModelPolicy":
        return cls(
            timeout_s=float(os.getenv("ITG_MODEL_TIMEOUT", cls.timeout_s)),
            retries=int(os.getenv("ITG_MODEL_RETRIES", cls.retries)),
            requests_per_minute=int(os.getenv("ITG_MODEL_RPM", cls.requests_per_minute)),
        )


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
                 sleep=asyncio.sleep):
        self.policy = policy or ModelPolicy.from_env()
        self.limiter = limiter or RateLimiter(self.policy.requests_per_minute)
        self._sleep = sleep
        self.consecutive_failures = 0
        self.circuit_open = False
        self.stats = {"calls": 0, "ok": 0, "retries": 0, "timeouts": 0, "failed": 0, "rate_wait_s": 0.0}

    async def call(self, make_call):
        """Run make_call() (returns a fresh awaitable each time) under the policy."""
        p = self.policy
        if self.circuit_open:
            raise ModelUnavailable("circuit open after repeated model failures")
        attempt = 0
        while True:
            self.stats["rate_wait_s"] += await self.limiter.acquire()
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
                raise
            except Exception as e:  # noqa: BLE001 - classify, then retry or give up
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
        return self.guard.call(lambda: inner(messages=messages, options=options, stream=False, **kwargs))
