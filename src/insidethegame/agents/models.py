"""Model client factory. The provider is a setting, not a code change.

ITG_MODEL_PROVIDER:
  none     (default) no model; the offline template analyst is used
  foundry  Microsoft Foundry project
           FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL (deployment name);
           auth: DefaultAzureCredential (Azure CLI, managed identity), or
           ITG_AZURE_AUTH=devicecode to sign in with a code from any browser
  openai   any OpenAI-compatible chat-completions endpoint, e.g. GitHub Models
           OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL (optional)

The Foundry and OpenAI paths are wired to Agent Framework's own clients but
have not been run against a live endpoint yet.
"""

from __future__ import annotations

import os


def make_chat_client():
    provider = os.getenv("ITG_MODEL_PROVIDER", "none").lower()
    if provider == "none":
        return None
    if provider == "foundry":
        from agent_framework.foundry import FoundryChatClient

        return FoundryChatClient(
            project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            model=os.environ["FOUNDRY_MODEL"],
            credential=_azure_credential(),
        )
    if provider == "openai":
        from agent_framework.openai import OpenAIChatCompletionClient

        return OpenAIChatCompletionClient(
            model=os.environ["OPENAI_MODEL"],
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )
    raise ValueError(f"Unknown ITG_MODEL_PROVIDER {provider!r}; use none, foundry or openai")


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
