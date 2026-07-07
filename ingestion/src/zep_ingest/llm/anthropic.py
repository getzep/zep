"""Anthropic adapter for the LLMClient protocol (optional: pip install zep-ingest[anthropic])."""

from typing import Any

from zep_ingest.exceptions import ZepDependencyError


class AnthropicLLM:
    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 200,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as error:
                raise ZepDependencyError(
                    "Anthropic", "pip install zep-ingest[anthropic]"
                ) from error
            client = anthropic.Anthropic()
        self.client: Any = client  # duck-typed so injected/mock clients work
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # read by block type, not position — responses may carry thinking blocks
        text = next((block.text for block in response.content if block.type == "text"), "")
        return str(text).strip()
