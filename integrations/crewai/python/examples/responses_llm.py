from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from crewai.llms.providers.openai.completion import OpenAICompletion
from crewai.utilities.types import LLMMessage
from openai.types.responses import Response
from pydantic import BaseModel, PrivateAttr

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


class ReasoningReplayOpenAICompletion(OpenAICompletion):
    """Replay encrypted reasoning items before their matching function calls."""

    model: str = "gpt-6-luna"
    api: Literal["responses"] = "responses"
    reasoning_effort: str = "low"
    store: bool = False
    auto_chain_reasoning: bool = True

    _reasoning_by_call: dict[str, list[dict[str, Any]]] = PrivateAttr(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        data.setdefault("model", "gpt-6-luna")
        data.setdefault("api", "responses")
        data.setdefault("reasoning_effort", "low")
        data.setdefault("store", False)
        data.setdefault("auto_chain_reasoning", True)
        super().__init__(**data)

    def _extract_reasoning_items(self, response: Response) -> list[Any]:
        reasoning_items = [
            item.model_dump(exclude_none=True)
            for item in response.output
            if item.type == "reasoning"
        ]
        function_calls = [item for item in response.output if item.type == "function_call"]
        if function_calls:
            self._reasoning_by_call[function_calls[0].call_id] = reasoning_items
        return []

    def _prepare_responses_params(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, BaseTool]] | None = None,
        response_model: type[BaseModel] | None = None,
    ) -> dict[str, Any]:
        params = super()._prepare_responses_params(messages, tools, response_model)
        input_items = params.get("input", [])
        active_call_ids = {
            item.get("call_id")
            for item in input_items
            if isinstance(item, dict) and item.get("type") == "function_call"
        }
        self._reasoning_by_call = {
            call_id: items
            for call_id, items in self._reasoning_by_call.items()
            if call_id in active_call_ids
        }
        reasoning_before: dict[int, list[dict[str, Any]]] = {}

        for index, item in enumerate(input_items):
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue

            call_id = item.get("call_id")
            reasoning_items = self._reasoning_by_call.get(call_id, [])
            if not reasoning_items:
                continue

            insert_at = index
            if index > 0:
                previous_item = input_items[index - 1]
                if isinstance(previous_item, dict) and previous_item.get("role") == "assistant":
                    insert_at -= 1
            reasoning_before.setdefault(insert_at, []).extend(reasoning_items)

        replayed_input: list[Any] = []
        for index, item in enumerate(input_items):
            replayed_input.extend(reasoning_before.get(index, []))
            replayed_input.append(item)
        params["input"] = replayed_input
        return params
