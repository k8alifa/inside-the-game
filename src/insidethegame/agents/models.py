"""Model client factory. The provider is a setting, not a code change.

ITG_MODEL_PROVIDER:
  (unset)        no model; offline template output
  foundry-local  Microsoft Foundry Local on your own computer. Free, no Azure
                 account. pip install -e ".[local]"; ITG_MODEL (default phi-4-mini).
                 The daily cap does not apply; the per-minute limiter does.
  foundry        Microsoft Foundry project on Azure (hosted option)
                 FOUNDRY_PROJECT_ENDPOINT, ITG_MODEL or FOUNDRY_MODEL;
                 auth: DefaultAzureCredential, or ITG_AZURE_AUTH=devicecode
  openai         any OpenAI-compatible chat-completions endpoint
                 OPENAI_API_KEY, ITG_MODEL or OPENAI_MODEL, OPENAI_BASE_URL (optional)
  github         retired: GitHub Models shut down on 2026-07-30. Selecting it
                 fails fast with ModelUnavailable and makes no network call.

Every client is wrapped with the resilience policy in resilience.py
(ITG_MODEL_TIMEOUT, ITG_MODEL_RETRIES, ITG_MODEL_RPM, ITG_MODEL_DAILY_CAP,
ITG_MODEL_MAX_PROMPT_TOKENS; usage is counted in ITG_USAGE_FILE), and every
reply must be real chat-completion JSON or it is rejected and falls back.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace

from .resilience import (DailyUsage, ModelGuard, ModelPolicy, ModelUnavailable, ResilienceMixin,
                         validated_http_client)

PROVIDERS = ("foundry-local", "foundry", "openai", "github")
FOUNDRY_LOCAL_DEFAULT_MODEL = "phi-4-mini"
GITHUB_RETIRED = ("GitHub Models was retired on 2026-07-30; "
                  "use ITG_MODEL_PROVIDER=foundry-local, foundry or openai")


@dataclass(frozen=True)
class ProviderConfig:
    provider: str | None
    model: str | None
    base_url: str | None = None


def provider_config() -> ProviderConfig:
    """Resolve the provider and model from the environment, without network or secrets."""
    provider = (os.getenv("ITG_MODEL_PROVIDER") or "").strip().lower() or None
    if provider in (None, "none", "template"):
        return ProviderConfig(None, None)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown ITG_MODEL_PROVIDER {provider!r}; use {', '.join(PROVIDERS[:3])}, or leave unset")
    model = os.getenv("ITG_MODEL")
    if provider == "foundry-local":
        return ProviderConfig(provider, model or os.getenv("FOUNDRY_LOCAL_MODEL") or FOUNDRY_LOCAL_DEFAULT_MODEL)
    if provider == "openai":
        return ProviderConfig(provider, model or os.getenv("OPENAI_MODEL"), os.getenv("OPENAI_BASE_URL") or None)
    if provider == "github":
        return ProviderConfig(provider, model)
    return ProviderConfig("foundry", model or os.getenv("FOUNDRY_MODEL"))


_GUARD: ModelGuard | None = None


def model_guard() -> ModelGuard:
    """One guard per process, so the rate limit and breaker cover every agent."""
    global _GUARD
    if _GUARD is None:
        policy = ModelPolicy.from_env()
        if provider_config().provider == "foundry-local":
            policy = replace(policy, daily_cap=0)  # runs on your own machine: no daily quota
        _GUARD = ModelGuard(policy, usage=DailyUsage.from_env())
    return _GUARD


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def make_chat_client(guard: ModelGuard | None = None):
    """The configured chat client, or None for template mode. Raises if the provider cannot be used."""
    cfg = provider_config()
    if cfg.provider is None:
        return None
    if cfg.provider == "github":
        raise ModelUnavailable(GITHUB_RETIRED)  # no network call
    guard = guard or model_guard()
    timeout = guard.policy.timeout_s + 5  # the guard enforces the real timeout

    if cfg.provider == "openai":
        from agent_framework.openai import OpenAIChatCompletionClient
        from openai import AsyncOpenAI

        class ResilientOpenAIChatClient(ResilienceMixin, OpenAIChatCompletionClient):
            pass

        key = _require("OPENAI_API_KEY")
        if not cfg.model:
            raise RuntimeError("set ITG_MODEL (or OPENAI_MODEL) for the openai provider")
        # SDK retries off: the guard owns retries, so attempts are counted once.
        sdk = AsyncOpenAI(api_key=key, base_url=cfg.base_url, max_retries=0, timeout=timeout,
                          http_client=validated_http_client(timeout))
        client = ResilientOpenAIChatClient(model=cfg.model, async_client=sdk)
        client.guard = guard
        return client

    if cfg.provider == "foundry-local":
        try:
            from agent_framework.foundry import FoundryLocalClient
        except ImportError as e:
            raise RuntimeError('Foundry Local support is not installed: pip install -e ".[local]"') from e
        from openai import AsyncOpenAI

        class ResilientFoundryLocalClient(ResilienceMixin, FoundryLocalClient):
            pass

        client = ResilientFoundryLocalClient(model=cfg.model)  # starts the local service, loads the model
        mgr = client.manager
        client.client = AsyncOpenAI(base_url=mgr.endpoint, api_key=mgr.api_key, max_retries=0, timeout=timeout,
                                    http_client=validated_http_client(timeout))
        client.guard = guard
        return client

    from agent_framework.foundry import FoundryChatClient

    class ResilientFoundryChatClient(ResilienceMixin, FoundryChatClient):
        pass

    client = ResilientFoundryChatClient(
        project_endpoint=_require("FOUNDRY_PROJECT_ENDPOINT"),
        model=cfg.model or _require("FOUNDRY_MODEL"),
        credential=_azure_credential(),
    )
    client.guard = guard
    return client


def client_or_template(log=None):
    """For CLIs: the model client, or None with a clear message so the run continues in template mode."""
    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
    try:
        return make_chat_client()
    except (ModelUnavailable, RuntimeError, ValueError) as e:
        log(f"Model unavailable: {e}. Continuing in template mode.")
        return None


_CREDENTIAL = None


def _azure_credential():
    """One credential per process, so device-code sign-in happens once."""
    global _CREDENTIAL
    if _CREDENTIAL is None:
        if os.getenv("ITG_AZURE_AUTH", "").lower() == "devicecode":
            from azure.identity import DeviceCodeCredential  # sync only; the client accepts both

            _CREDENTIAL = DeviceCodeCredential()
        else:
            from azure.identity.aio import DefaultAzureCredential

            _CREDENTIAL = DefaultAzureCredential()
    return _CREDENTIAL
