from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from openai import OpenAI
from zep_cloud import Zep

from anthropic_client import AnthropicResponsesClient
from call_ledger import openai_http_client, zep_http_client


def zep_api_url(api_url: str) -> str:
    root = api_url.rstrip("/").removesuffix("/api/v4")
    return f"{root}/api/v4"


@dataclass(frozen=True)
class Config:
    api_url: str | None
    api_key: str
    openai_api_key: str
    concurrency: int


def zep_client(api_key: str, api_url: str | None = None) -> Zep:
    options: dict[str, Any] = {
        "api_key": api_key,
        "httpx_client": zep_http_client(),
    }
    if api_url:
        options["base_url"] = zep_api_url(api_url)
    return Zep(**options)


def load_config(*, require_zep: bool = True, require_openai: bool = True) -> Config:
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key and require_openai:
        raise RuntimeError("OPENAI_API_KEY is required for tau2-bench evaluation runs")
    api_key = os.getenv("ZEP_API_KEY") or ""
    if not api_key and require_zep:
        raise RuntimeError("ZEP_API_KEY is required for Zep Agent Skills runs")
    return Config(
        api_url=os.getenv("ZEP_BASE_URL"),
        api_key=api_key,
        openai_api_key=openai_api_key or "",
        concurrency=int(os.getenv("TAU_BENCH_SKILLS_CONCURRENCY", "4")),
    )


class _RoutedResponses:
    def __init__(self, responses: Any, extra_body: dict[str, Any]) -> None:
        self._responses = responses
        self._extra_body = extra_body

    def create(self, **kwargs: Any) -> Any:
        return self._responses.create(extra_body=self._extra_body, **kwargs)


class ProviderRoutedClient:
    """An OpenAI-compatible client that adds OpenRouter provider constraints."""

    def __init__(self, client: OpenAI, provider: str | None = None) -> None:
        provider_config: dict[str, Any] = {"require_parameters": True}
        if provider:
            provider_config = {
                "only": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        self.responses = _RoutedResponses(
            client.responses,
            {"provider": provider_config},
        )


def _is_openrouter_base_url(base_url: str) -> bool:
    hostname = urlsplit(base_url).hostname
    return bool(hostname and (hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai")))


def agent_client(
    default: OpenAI,
    base_url: str | None,
    api_key_env: str,
    provider: str | None,
    model: str = "",
) -> Any:
    """Return the client for the benchmark agent. The user simulator keeps the default client."""
    if model.startswith("claude-"):
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for a Claude agent model")
        return AnthropicResponsesClient(anthropic_key)
    if not base_url:
        return default
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} is required for --agent-base-url")
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=6,
        http_client=openai_http_client("model"),
    )
    if provider:
        return ProviderRoutedClient(client, provider)
    if _is_openrouter_base_url(base_url):
        return ProviderRoutedClient(client)
    return client
