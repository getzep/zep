"""OpenAI-format adapters for the LLMClient protocol.

OpenAILLM targets OpenAI itself. OpenAICompatibleLLM is the universal
connector: point it at any OpenAI-compatible /chat/completions endpoint —
LiteLLM, Ollama, vLLM, OpenRouter, Together, Groq, Azure-compatible proxies —
which is the pattern Zep's own docs recommend for bring-your-own-provider
setups. Both are optional conveniences: anything implementing
``complete(prompt) -> str`` satisfies the LLMClient protocol directly.

Optional dependency: pip install "zep-ingest[openai]" (not needed when you
pass a pre-constructed client).
"""

from typing import Any

from zep_ingest._validation import require_int_range
from zep_ingest.exceptions import ConfigurationError, ZepDependencyError


def _make_openai_client(**kwargs: Any) -> Any:
    try:
        import openai
    except ImportError as error:
        raise ZepDependencyError("OpenAI", "pip install zep-ingest[openai]") from error
    return openai.OpenAI(**kwargs)


class OpenAILLM:
    """Use low reasoning for Luna by default. Other models omit it.

    Set ``reasoning_effort`` to an explicit value to override the model
    default, or to ``None`` to omit the parameter for any model.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = "gpt-6-luna",
        max_tokens: int = 200,
        reasoning_effort: str | None = "auto",
    ) -> None:
        require_int_range("max_tokens", max_tokens, minimum=1)
        self.client = client if client is not None else _make_openai_client()
        self.model = model
        self.max_tokens = max_tokens
        self.reasoning_effort = (
            ("low" if model == "gpt-6-luna" else None)
            if reasoning_effort == "auto"
            else reasoning_effort
        )

    def complete(self, prompt: str) -> str:
        options: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            options["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=self.max_tokens,
            **options,
        )
        return (response.choices[0].message.content or "").strip()


class OpenAICompatibleLLM(OpenAILLM):
    """Any OpenAI-compatible endpoint. ``model`` and (unless a client is
    passed) ``base_url`` are provider-specific, so both are required.
    ``reasoning_effort`` is not sent unless you set it, because many
    compatible endpoints do not accept it.

    Examples:
        OpenAICompatibleLLM(model="claude-haiku-4-5", base_url="http://localhost:4000", api_key="...")   # LiteLLM proxy
        OpenAICompatibleLLM(model="llama3.1:70b", base_url="http://localhost:11434/v1", api_key="ollama") # Ollama
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        client: Any | None = None,
        max_tokens: int = 200,
        reasoning_effort: str | None = None,
    ) -> None:
        require_int_range("max_tokens", max_tokens, minimum=1)
        if client is None:
            if not isinstance(base_url, str) or not base_url.strip():
                raise ConfigurationError(
                    "base_url is required when OpenAICompatibleLLM constructs its client"
                )
            client = _make_openai_client(base_url=base_url, api_key=api_key)
        super().__init__(
            client, model=model, max_tokens=max_tokens, reasoning_effort=reasoning_effort
        )
