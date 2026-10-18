"""Model client factory. The provider is a setting, not a code change.

ITG_MODEL_PROVIDER:
  (unset)  no model; offline template output
  github   GitHub Models, free tier, OpenAI-compatible
           GITHUB_TOKEN (fine-grained token with models:read)
           ITG_MODEL (default openai/gpt-4.1-mini)
  openai   any OpenAI-compatible chat-completions endpoint
           OPENAI_API_KEY, ITG_MODEL or OPENAI_MODEL, OPENAI_BASE_URL (optional)
  foundry  Microsoft Foundry project (kept, untested)
           FOUNDRY_PROJECT_ENDPOINT, ITG_MODEL or FOUNDRY_MODEL;
           auth: DefaultAzureCredential, or ITG_AZURE_AUTH=devicecode

Every client is wrapped with the resilience policy in resilience.py
(ITG_MODEL_TIMEOUT, ITG_MODEL_RETRIES, ITG_MODEL_RPM, ITG_MODEL_DAILY_CAP,
ITG_MODEL_MAX_PROMPT_TOKENS; usage is counted in ITG_USAGE_FILE).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .resilience import DailyUsage, ModelGuard, ModelPolicy, ResilienceMixin

GITHUB_MODELS_URL = "https://models.github.ai/inference"
GITHUB_DEFAULT_MODEL = "openai/gpt-4.1-mini"
PROVIDERS = ("github", "openai", "foundry")


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
        raise ValueError(f"Unknown ITG_MODEL_PROVIDER {provider!r}; use github, openai, foundry, or leave unset")
    model = os.getenv("ITG_MODEL")
    if provider == "github":
        return ProviderConfig("github", model or GITHUB_DEFAULT_MODEL, GITHUB_MODELS_URL)
    if provider == "openai":
        return ProviderConfig("openai", model or os.getenv("OPENAI_MODEL"), os.getenv("OPENAI_BASE_URL") or None)
    return ProviderConfig("foundry", model or os.getenv("FOUNDRY_MODEL"))


_GUARD: ModelGuard | None = None


def model_guard() -> ModelGuard:
    """One guard per process, so the rate limit and breaker cover every agent."""
    global _GUARD
    if _GUARD is None:
        _GUARD = ModelGuard(ModelPolicy.from_env(), usage=DailyUsage.from_env())
    return _GUARD


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def make_chat_client(guard: ModelGuard | None = None):
    cfg = provider_config()
    if cfg.provider is None:
        return None
    guard = guard or model_guard()
    timeout = guard.policy.timeout_s + 5  # the guard enforces the real timeout

    if cfg.provider in ("github", "openai"):
        from agent_framework.openai import OpenAIChatCompletionClient
        from openai import AsyncOpenAI

        class ResilientOpenAIChatClient(ResilienceMixin, OpenAIChatCompletionClient):
            pass

        key = _require("GITHUB_TOKEN" if cfg.provider == "github" else "OPENAI_API_KEY")
        if not cfg.model:
            raise RuntimeError("set ITG_MODEL (or OPENAI_MODEL) for the openai provider")
        # SDK retries off: the guard owns retries, so attempts are counted once.
        sdk = AsyncOpenAI(api_key=key, base_url=cfg.base_url, max_retries=0, timeout=timeout)
        client = ResilientOpenAIChatClient(model=cfg.model, async_client=sdk)
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
