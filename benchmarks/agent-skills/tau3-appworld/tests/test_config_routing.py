from typing import Any
from unittest.mock import MagicMock

import pytest

import config


def _agent_client_for_test(
    monkeypatch: Any, base_url: str, provider: str | None = None
) -> tuple[MagicMock, Any]:
    openai_client = MagicMock()
    monkeypatch.setattr(config, "OpenAI", lambda **_: openai_client)
    monkeypatch.setattr(config, "openai_http_client", lambda *_: object())
    monkeypatch.setenv("AGENT_API_KEY", "test-agent-key")
    agent = config.agent_client(
        MagicMock(),
        base_url,
        "AGENT_API_KEY",
        provider,
        "nvidia/nemotron-3.5-lightning",
    )
    return openai_client, agent


@pytest.mark.parametrize(
    "base_url",
    [
        "https://openrouter.ai/api/v1",
        "https://api.openrouter.ai/v1",
    ],
)
def test_agent_client_requires_parameters_for_unpinned_openrouter(
    monkeypatch: Any, base_url: str
) -> None:
    openai_client, agent = _agent_client_for_test(monkeypatch, base_url)

    agent.responses.create(model="nvidia/nemotron-3.5-lightning", input=[])

    openai_client.responses.create.assert_called_once_with(
        extra_body={"provider": {"require_parameters": True}},
        model="nvidia/nemotron-3.5-lightning",
        input=[],
    )


def test_agent_client_keeps_pinned_openrouter_provider(monkeypatch: Any) -> None:
    openai_client, agent = _agent_client_for_test(
        monkeypatch, "https://openrouter.ai/api/v1", "nvidia/nemotron-3.5-lightning"
    )

    agent.responses.create(model="nvidia/nemotron-3.5-lightning", input=[])

    openai_client.responses.create.assert_called_once_with(
        extra_body={
            "provider": {
                "only": ["nvidia/nemotron-3.5-lightning"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
        model="nvidia/nemotron-3.5-lightning",
        input=[],
    )


def test_agent_client_does_not_add_provider_parameters_for_other_base_urls(
    monkeypatch: Any,
) -> None:
    openai_client, agent = _agent_client_for_test(monkeypatch, "https://example.com/v1")

    assert agent is openai_client
    agent.responses.create(model="model", input=[])

    openai_client.responses.create.assert_called_once_with(model="model", input=[])
