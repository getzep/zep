from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from pydantic import create_model
from stirrup.core.agent import Agent as StirrupAgent
from stirrup.core.models import AssistantMessage, Tool, ToolCall, ToolProvider, ToolResult

from eog.agent import build_agent_config
from eog.dataset import load_tasks
from eog.providers.openai_route import OpenAIRoute
from eog.stirrup_constants import FINISH_TOOL_NAME

EmptyParams = create_model("EmptyParams")


class FakeToolProvider(ToolProvider):
    def __init__(self, names: tuple[str, ...]) -> None:
        self.tools = [
            Tool(
                name=name,
                description="Fake benchmark tool.",
                parameters=EmptyParams,
                executor=lambda _: ToolResult(content="ok"),
            )
            for name in names
        ]

    async def __aenter__(self) -> list[Tool]:
        return self.tools


class FakeStirrupClient:
    model_slug = "fake-model"
    context_window_tokens = 200_000

    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.tools: dict[str, Tool] = {}

    async def generate(self, messages: list[Any], tools: dict[str, Tool]) -> AssistantMessage:
        self.messages = messages
        self.tools = tools
        return AssistantMessage(
            blocks=[
                ToolCall(
                    name=FINISH_TOOL_NAME,
                    arguments='{"reason":"done","paths":[]}',
                    tool_call_id="finish-call",
                )
            ]
        )


class FakeResponses:
    def __init__(self) -> None:
        self.body: dict[str, Any] = {}

    def create(self, **body: Any) -> SimpleNamespace:
        self.body = body
        return SimpleNamespace(
            output=[],
            usage=SimpleNamespace(
                model_dump=lambda: {
                    "input_tokens": 0,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 0,
                    "output_tokens_details": {"reasoning_tokens": 0},
                }
            ),
        )

    @property
    def with_raw_response(self) -> SimpleNamespace:
        return SimpleNamespace(
            create=lambda **body: SimpleNamespace(status_code=200, parse=lambda: self.create(**body))
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_pinned_stirrup_and_route_match_no_skills_config() -> None:
    tasks = [next(task for task in load_tasks() if task.domain == domain) for domain in ("calendar", "csm")]
    for index, task in enumerate(tasks):
        available_tools = [
            {"name": name, "description": f"{name} tool", "inputSchema": {"type": "object", "properties": {}}}
            for name in task.selected_tools
        ]
        config = build_agent_config(task, available_tools)
        client = FakeStirrupClient()
        provider = FakeToolProvider(tuple(task.selected_tools))
        agent = StirrupAgent(
            client=client,
            name=f"parity-{index}",
            max_turns=config.max_turns,
            system_prompt=task.system_prompt,
            tools=[provider],
            turns_remaining_warning_threshold=config.turns_remaining_warning_threshold,
        )

        async def run_agent(agent=agent, user_prompt=task.user_prompt) -> None:
            async with agent.session() as session:
                await session.run(user_prompt)

        asyncio.run(run_agent())
        assert client.messages[0].content == config.system
        assert client.messages[1].content == task.user_prompt
        assert list(client.tools) == [FINISH_TOOL_NAME, *task.selected_tools]
        assert client.tools[FINISH_TOOL_NAME].parameters.model_json_schema() == config.tools[0]["inputSchema"]
        assert agent._max_turns == config.max_turns

        fake_openai = FakeOpenAI()
        route = OpenAIRoute(client=fake_openai)
        route.complete(
            config.system,
            [{"role": "user", "content": task.user_prompt}],
            list(config.tools),
            effort="low",
            task_id=task.task_id,
        )
        body = fake_openai.responses.body
        assert body["instructions"] == client.messages[0].content
        assert body["input"][0] == {"role": "user", "content": client.messages[1].content}
        assert [tool["name"] for tool in body["tools"]] == list(client.tools)
        assert body["tools"][0]["parameters"] == client.tools[FINISH_TOOL_NAME].parameters.model_json_schema()
        assert len(body["tools"]) == len(config.tools)
        for source_tool, provider_tool in zip(config.tools, body["tools"], strict=True):
            assert provider_tool["parameters"] == source_tool["inputSchema"]
