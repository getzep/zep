from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any, Literal

from autogen_core import FunctionCall, Image
from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    CreateResult,
    FunctionExecutionResultMessage,
    ModelFamily,
    ModelInfo,
    RequestUsage,
    SystemMessage,
    UserMessage,
)
from autogen_core.tools import Tool, ToolSchema
from openai import AsyncOpenAI
from pydantic import BaseModel


class OpenAIResponsesChatCompletionClient(ChatCompletionClient):
    def __init__(
        self,
        model: str = "gpt-6-luna",
        reasoning_effort: str = "low",
        client: AsyncOpenAI | None = None,
        **client_kwargs: Any,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = client or AsyncOpenAI(**client_kwargs)
        self._reasoning_by_call: dict[str, list[dict[str, Any]]] = {}
        self._actual_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)
        self._total_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)

    async def create(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Tool | ToolSchema] = (),
        tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
        json_output: bool | type[BaseModel] | None = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Any = None,
    ) -> CreateResult:
        input_items: list[dict[str, Any]] = []
        instructions: list[str] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                instructions.append(message.content)
            elif isinstance(message, UserMessage):
                input_items.extend(self._user_input(message.content))
            elif isinstance(message, AssistantMessage):
                if isinstance(message.content, str):
                    input_items.append({"role": "assistant", "content": message.content})
                else:
                    if message.content:
                        input_items.extend(self._reasoning_by_call.pop(message.content[0].id, []))
                    input_items.extend(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in message.content
                    )
            elif isinstance(message, FunctionExecutionResultMessage):
                input_items.extend(
                    {
                        "type": "function_call_output",
                        "call_id": result.call_id,
                        "output": result.content,
                    }
                    for result in message.content
                )
            else:
                raise ValueError(f"Unsupported AutoGen message type: {type(message).__name__}")

        params: dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "reasoning": {"effort": self._reasoning_effort},
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if instructions:
            params["instructions"] = "\n\n".join(instructions)
        if tools:
            params["tools"] = [self._tool_schema(tool) for tool in tools]
            params["tool_choice"] = self._tool_choice(tool_choice)
        if json_output is True:
            params["text"] = {"format": {"type": "json_object"}}
        elif isinstance(json_output, type) and issubclass(json_output, BaseModel):
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": json_output.__name__,
                    "schema": json_output.model_json_schema(),
                    "strict": True,
                }
            }

        forbidden = {
            "input",
            "previous_response_id",
            "temperature",
            "top_p",
            "seed",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "logprobs",
            "top_logprobs",
            "stop",
            "best_of",
            "top_k",
            "min_p",
            "typical_p",
            "n",
        }
        params.update(
            {key: value for key, value in extra_create_args.items() if key not in forbidden}
        )
        params.update(
            model=self._model,
            reasoning={"effort": self._reasoning_effort},
            store=False,
            include=["reasoning.encrypted_content"],
        )
        params.pop("previous_response_id", None)

        response = await self._client.responses.create(**params)
        output = response.output
        calls = [item for item in output if self._get(item, "type") == "function_call"]
        reasoning_items = [
            self._dump(item) for item in output if self._get(item, "type") == "reasoning"
        ]
        usage = response.usage
        input_tokens = int(self._get(usage, "input_tokens", 0) or 0)
        output_tokens = int(self._get(usage, "output_tokens", 0) or 0)
        self._actual_usage = RequestUsage(
            prompt_tokens=input_tokens, completion_tokens=output_tokens
        )
        self._total_usage = RequestUsage(
            prompt_tokens=self._total_usage.prompt_tokens + input_tokens,
            completion_tokens=self._total_usage.completion_tokens + output_tokens,
        )

        if calls:
            function_calls = [
                FunctionCall(
                    id=str(self._get(item, "call_id", "")),
                    name=str(self._get(item, "name", "")),
                    arguments=str(self._get(item, "arguments", "{}")),
                )
                for item in calls
            ]
            self._reasoning_by_call[function_calls[0].id] = reasoning_items
            finish_reason: Literal["stop", "length", "function_calls"] = "function_calls"
            content: str | list[FunctionCall] = function_calls
        else:
            content = self._output_text(output)
            incomplete = self._get(response, "incomplete_details")
            finish_reason = (
                "length"
                if self._get(response, "status") == "incomplete"
                and self._get(incomplete, "reason") == "max_output_tokens"
                else "stop"
            )

        return CreateResult(
            content=content,
            finish_reason=finish_reason,
            usage=self._actual_usage,
            cached=False,
            thought=self._reasoning_summary(output),
        )

    async def create_stream(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Tool | ToolSchema] = (),
        tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
        json_output: bool | type[BaseModel] | None = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Any = None,
    ) -> AsyncGenerator[CreateResult, None]:
        yield await self.create(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )

    async def close(self) -> None:
        await self._client.close()

    def actual_usage(self) -> RequestUsage:
        return self._actual_usage

    def total_usage(self) -> RequestUsage:
        return self._total_usage

    def count_tokens(
        self, messages: Sequence[Any], *, tools: Sequence[Tool | ToolSchema] = ()
    ) -> int:
        return max(1, len(str(messages)) // 4)

    def remaining_tokens(
        self, messages: Sequence[Any], *, tools: Sequence[Tool | ToolSchema] = ()
    ) -> int:
        return max(0, 128_000 - self.count_tokens(messages, tools=tools))

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "vision": False,
            "function_calling": True,
            "json_output": True,
        }

    @property
    def model_info(self) -> ModelInfo:
        return {
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True,
            "family": ModelFamily.UNKNOWN,
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
    def _user_input(cls, content: str | list[str | Image]) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"role": "user", "content": content}]
        parts: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                parts.append({"type": "input_text", "text": part})
            elif isinstance(part, Image):
                parts.append({"type": "input_image", "image_url": part.data_uri})
            else:
                raise ValueError(f"Unsupported user content type: {type(part).__name__}")
        return [{"role": "user", "content": parts}]

    @classmethod
    def _tool_schema(cls, tool: Tool | ToolSchema) -> dict[str, Any]:
        schema = tool.schema if isinstance(tool, Tool) else tool
        return {
            "type": "function",
            "name": cls._get(schema, "name"),
            "description": cls._get(schema, "description", ""),
            "parameters": cls._get(schema, "parameters", {}),
            "strict": False,
        }

    @classmethod
    def _tool_choice(cls, tool_choice: Tool | Literal["auto", "required", "none"]) -> Any:
        if isinstance(tool_choice, str):
            return tool_choice
        return {"type": "function", "name": tool_choice.name}

    @classmethod
    def _output_text(cls, output: Sequence[Any]) -> str:
        text_parts: list[str] = []
        for item in output:
            if cls._get(item, "type") != "message":
                continue
            for part in cls._get(item, "content", []):
                if cls._get(part, "type") == "output_text":
                    text_parts.append(str(cls._get(part, "text", "")))
        return "".join(text_parts)

    @classmethod
    def _reasoning_summary(cls, output: Sequence[Any]) -> str | None:
        summaries: list[str] = []
        for item in output:
            if cls._get(item, "type") != "reasoning":
                continue
            for part in cls._get(item, "summary", []):
                if cls._get(part, "type") == "summary_text":
                    summaries.append(str(cls._get(part, "text", "")))
        return "\n".join(summaries) or None
