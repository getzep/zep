from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.user.user_simulator import UserSimulator
from tau2.user.user_simulator_base import UserState, ValidUserInputMessage

from call_ledger import openai_http_client
from tracing import traced_responses_create


class ResponsesUserSimulator(UserSimulator[UserState]):
    """Use tau2's persona and state with the OpenAI Responses tool protocol."""

    def set_seed(self, seed: int) -> None:
        # Responses does not accept a seed parameter.
        pass

    def _generate_next_message(
        self, message: ValidUserInputMessage, state: UserState
    ) -> UserMessage:
        if isinstance(message, AssistantMessage) and message.is_audio:
            raise ValueError("The Responses user simulator supports text only")
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        elif isinstance(message, ToolMessage) or message.has_content() or message.is_tool_call():
            state.messages.append(message)

        inputs: list[dict[str, Any]] = [
            {"role": "system", "content": item.content or ""} for item in state.system_messages
        ]
        for original, flipped in zip(state.messages, state.flip_roles(), strict=True):
            output = (getattr(original, "raw_data", None) or {}).get("responses_output")
            if isinstance(original, UserMessage) and output is not None:
                inputs.extend(output)
            elif isinstance(flipped, ToolMessage):
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": flipped.id,
                        "output": flipped.content,
                    }
                )
            else:
                if flipped.content:
                    inputs.append({"role": flipped.role, "content": flipped.content})
                for call in getattr(flipped, "tool_calls", None) or []:
                    inputs.append(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        }
                    )

        options = dict(self.llm_args)
        effort = options.pop("reasoning_effort", "low")
        if effort != "low" or options:
            raise ValueError("The Luna user simulator requires low reasoning without sampling args")
        tools = []
        for tool in self.tools or []:
            tools.append({"type": "function", **tool.openai_schema["function"], "strict": False})
        client = OpenAI(http_client=openai_http_client("tau2-user-simulator"))
        try:
            response = traced_responses_create(
                client,
                "tau2-user-simulator",
                {
                    "model": self.llm,
                    "input": inputs,
                    "tools": tools,
                    "reasoning": {"effort": "low"},
                    "include": ["reasoning.encrypted_content"],
                    "store": False,
                },
            )
        finally:
            client.close()
        if response.status != "completed":
            raise ValueError(f"The user simulator response is {response.status}")
        calls = [
            ToolCall(
                id=item.call_id,
                name=item.name,
                arguments=json.loads(item.arguments),
                requestor="user",
            )
            for item in response.output
            if item.type == "function_call"
        ]
        if not response.output_text and not calls:
            raise ValueError("The user simulator returned no text or tool calls")
        usage = response.usage
        return UserMessage(
            role="user",
            content=response.output_text or None,
            tool_calls=calls or None,
            usage={
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
            }
            if usage
            else None,
            raw_data={
                "responses_output": [item.model_dump(exclude_none=True) for item in response.output]
            },
        )
