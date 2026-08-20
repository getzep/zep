"""OrcaRouter adapter for the LLMClient protocol (optional: pip install zep-ingest[openai]).

[OrcaRouter](https://www.orcarouter.ai) is an OpenAI-compatible gateway that
routes to the best model for each request. It also runs gateway-level,
zero-trust security for AI agents on the same endpoint — screening every
prompt/response and governing every tool call on a default-deny basis, with
no application code changes.

``OrcaRouterLLM`` is a named convenience built on ``OpenAICompatibleLLM``:
it pre-fills the gateway base URL and the adaptive ``orcarouter/auto`` model
so a contextualizer can be wired up in one line:

    from zep_ingest.llm.orcarouter import OrcaRouterLLM

    LLMContextualizer(OrcaRouterLLM())  # reads ORCAROUTER_API_KEY

The API key is read from the ``ORCAROUTER_API_KEY`` environment variable
(prefix ``sk-orca-``) unless one is passed explicitly.
"""

from typing import Any

from zep_ingest.llm.openai import OpenAICompatibleLLM

DEFAULT_ORCAROUTER_BASE_URL = "https://api.orcarouter.ai/v1"
DEFAULT_ORCAROUTER_MODEL = "orcarouter/auto"


class OrcaRouterLLM(OpenAICompatibleLLM):
    """Named adapter for OrcaRouter's OpenAI-compatible gateway.

    Defaults to ``https://api.orcarouter.ai/v1`` with the adaptive
    ``orcarouter/auto`` model. The API key defaults to the
    ``ORCAROUTER_API_KEY`` environment variable.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_ORCAROUTER_MODEL,
        base_url: str = DEFAULT_ORCAROUTER_BASE_URL,
        api_key: str | None = None,
        client: Any | None = None,
        max_tokens: int = 200,
    ) -> None:
        import os

        super().__init__(
            model=model,
            base_url=base_url,
            api_key=os.environ.get("ORCAROUTER_API_KEY") if api_key is None else api_key,
            client=client,
            max_tokens=max_tokens,
        )
