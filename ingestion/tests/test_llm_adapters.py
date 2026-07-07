"""Tests for the OpenAI/Anthropic LLM adapters (fake SDK clients, no real deps)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zep_ingest.exceptions import ZepDependencyError


class TestOpenAIAdapter:
    def test_complete_uses_chat_completions(self):
        from zep_ingest.llm.openai import OpenAILLM

        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  the context  "))]
        )
        llm = OpenAILLM(client=client, model="gpt-5-mini")
        assert llm.complete("prompt text") == "the context"
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-5-mini"
        assert kwargs["messages"] == [{"role": "user", "content": "prompt text"}]

    def test_missing_sdk_raises_dependency_error(self, monkeypatch):
        from zep_ingest.llm.openai import OpenAILLM

        monkeypatch.setitem(__import__("sys").modules, "openai", None)
        with pytest.raises(ZepDependencyError, match="zep-ingest\\[openai\\]"):
            OpenAILLM()


class TestAnthropicAdapter:
    def test_complete_uses_messages(self):
        from zep_ingest.llm.anthropic import AnthropicLLM

        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=" context ")]
        )
        llm = AnthropicLLM(client=client)
        assert llm.complete("prompt") == "context"
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku-4-5"
        assert kwargs["max_tokens"] == 200

    def test_reads_first_text_block_not_positional(self):
        from zep_ingest.llm.anthropic import AnthropicLLM

        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="..."),
                SimpleNamespace(type="text", text="the context"),
            ]
        )
        assert AnthropicLLM(client=client).complete("p") == "the context"

    def test_missing_sdk_raises_dependency_error(self, monkeypatch):
        from zep_ingest.llm.anthropic import AnthropicLLM

        monkeypatch.setitem(__import__("sys").modules, "anthropic", None)
        with pytest.raises(ZepDependencyError, match="zep-ingest\\[anthropic\\]"):
            AnthropicLLM()


class TestOpenAICompatibleAdapter:
    """The universal connector: any OpenAI-compatible /chat/completions endpoint
    (LiteLLM, Ollama, vLLM, OpenRouter, Together, ...)."""

    def test_requires_model_and_base_url(self):
        from zep_ingest.llm.openai import OpenAICompatibleLLM

        with pytest.raises(TypeError):
            OpenAICompatibleLLM()  # type: ignore[call-arg]

    def test_constructs_openai_client_with_base_url(self, monkeypatch):
        import sys

        from zep_ingest.llm.openai import OpenAICompatibleLLM

        fake_openai = MagicMock()
        monkeypatch.setitem(sys.modules, "openai", fake_openai)
        llm = OpenAICompatibleLLM(
            model="llama3.1:70b", base_url="http://localhost:11434/v1", api_key="k"
        )
        fake_openai.OpenAI.assert_called_once_with(
            base_url="http://localhost:11434/v1", api_key="k"
        )
        assert llm.model == "llama3.1:70b"

    def test_complete_uses_chat_completions(self):
        from zep_ingest.llm.openai import OpenAICompatibleLLM

        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ctx"))]
        )
        llm = OpenAICompatibleLLM(model="my-model", client=client)
        assert llm.complete("prompt") == "ctx"
        assert client.chat.completions.create.call_args.kwargs["model"] == "my-model"

    def test_missing_sdk_raises_dependency_error(self, monkeypatch):
        import sys

        from zep_ingest.llm.openai import OpenAICompatibleLLM

        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(ZepDependencyError, match="zep-ingest\\[openai\\]"):
            OpenAICompatibleLLM(model="m", base_url="http://localhost:4000")
