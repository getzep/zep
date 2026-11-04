from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autogen.oai.client import ModelClient
from openai import OpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageToolCall,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import Function
from openai.types.completion_usage import CompletionUsage


class OpenAIResponsesModelClient(ModelClient):
    RESPONSE_USAGE_KEYS = ["prompt_tokens", "completion_tokens", "total_tokens", "cost", "model"]

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        self.model = config.get("model", "gpt-6-luna")
        self.reasoning_effort = config.get("reasoning_effort", "low")
        self._client = kwargs.pop("client", None) or OpenAI(
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            **kwargs,
        )
        self._reasoning_by_call: dict[str, list[dict[str, Any]]] = {}

    def create(self, params: dict[str, Any]) -> ChatCompletion:
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for message in params.get("messages", []):
            role = message.get("role", "user")
            content = message.get("content")
            if role == "system":
                if content:
                    instructions.append(str(content))
            elif role == "assistant" and message.get("tool_calls"):
                tool_calls = message["tool_calls"]
                input_items.extend(self._reasoning_by_call.get(tool_calls[0]["id"], []))
                if content:
                    input_items.append({"role": "assistant", "content": content})
                input_items.extend(self._function_call(call) for call in tool_calls)
            elif role in ("user", "assistant") and content:
                input_items.append({"role": role, "content": content})
            elif role == "tool":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.get("tool_call_id", ""),
                        "output": str(content or ""),
                    }
                )

        active_call_ids = {
            item["call_id"] for item in input_items if item.get("type") == "function_call"
        }
        self._reasoning_by_call = {
            call_id: items
            for call_id, items in self._reasoning_by_call.items()
            if call_id in active_call_ids
        }

        create_params: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "reasoning": {"effort": self.reasoning_effort},
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if instructions:
            create_params["instructions"] = "\n\n".join(instructions)
        if params.get("tools"):
            create_params["tools"] = [self._response_tool(tool) for tool in params["tools"]]
        if "tool_choice" in params:
            create_params["tool_choice"] = self._tool_choice(params["tool_choice"])
        if "max_tokens" in params:
            create_params["max_output_tokens"] = params["max_tokens"]

        response = self._client.responses.create(**create_params)
        output = response.output
        function_calls = [item for item in output if self._get(item, "type") == "function_call"]
        reasoning_items = [
            self._dump(item) for item in output if self._get(item, "type") == "reasoning"
        ]
        message_text = self._output_text(output)
        tool_calls = [
            ChatCompletionMessageToolCall(
                id=str(self._get(call, "call_id", "")),
                type="function",
                function=Function(
                    name=str(self._get(call, "name", "")),
                    arguments=str(self._get(call, "arguments", "{}")),
                ),
            )
            for call in function_calls
        ]
        if tool_calls:
            self._reasoning_by_call[tool_calls[0].id] = reasoning_items

        input_tokens = int(self._get(response.usage, "input_tokens", 0) or 0)
        output_tokens = int(self._get(response.usage, "output_tokens", 0) or 0)
        return ChatCompletion(
            id=str(self._get(response, "id", "response")),
            choices=[
                Choice(
                    finish_reason="tool_calls" if tool_calls else "stop",
                    index=0,
                    message=ChatCompletionMessage(
                        role="assistant",
                        content=message_text or None,
                        tool_calls=tool_calls or None,
                    ),
                )
            ],
            created=int(self._get(response, "created_at", 0) or 0),
            model=str(self._get(response, "model", self.model)),
            object="chat.completion",
            usage=CompletionUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    def message_retrieval(self, response: ChatCompletion) -> list[Any]:
        return [choice.message for choice in response.choices]

    def cost(self, response: ChatCompletion) -> float:
        return 0.0

    @staticmethod
    def get_usage(response: ChatCompletion) -> dict[str, Any]:
        usage = response.usage
        return {
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "total_tokens": usage.total_tokens if usage else 0,
            "cost": 0.0,
            "model": response.model,
        }

    @staticmethod
    def _get(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(key, default)
        return getattr(value, key, default)

    @classmethod
    def _dump(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(exclude_none=True)
        return vars(value)

    @classmethod
    def _function_call(cls, call: Mapping[str, Any]) -> dict[str, Any]:
        function = call.get("function", {})
        return {
            "type": "function_call",
            "call_id": call.get("id", ""),
            "name": function.get("name", ""),
            "arguments": function.get("arguments") or "{}",
        }

    @classmethod
    def _response_tool(cls, tool: Mapping[str, Any]) -> dict[str, Any]:
        function = tool.get("function", {})
        return {
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {}),
            "strict": False,
        }

    @classmethod
    def _tool_choice(cls, choice: Any) -> Any:
        if isinstance(choice, str):
            return choice
        if isinstance(choice, Mapping) and choice.get("type") == "function":
            function = choice.get("function", {})
            return {"type": "function", "name": function.get("name", "")}
        return choice

    @classmethod
    def _output_text(cls, output: list[Any]) -> str:
        text: list[str] = []
        for item in output:
            if cls._get(item, "type") != "message":
                continue
            for part in cls._get(item, "content", []):
                if cls._get(part, "type") == "output_text":
                    text.append(str(cls._get(part, "text", "")))
        return "".join(text)
