"""Google ADK adapter for the shared invented banking tools."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models.anthropic_llm import AnthropicGenerateContentConfig, AnthropicLlm
from google.adk.models.base_llm import BaseLlm
from google.adk.plugins import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

from banking import Case
from episode import EpisodeResult
from prompts import SYSTEM, TEACHING_REQUEST, USER
from tools import BankingTools, ToolError
from zep_skills import TraceRecorder

APP_NAME = "agent-skills-frameworks"
TOOL_NAMES = (
    "get_case",
    "list_documents",
    "read_document",
    "calculator",
    "quote_interest",
    "compare_packages",
    "credit_interest",
    "file_discrepancy_report",
    "read_skill",
)


def build_config(effort: str) -> AnthropicGenerateContentConfig:
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Effort must be none, low, medium, high, xhigh, or max.")
    anthropic_effort = (
        None if effort == "none" else cast(Literal["low", "medium", "high", "xhigh", "max"], effort)
    )
    return AnthropicGenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0 if effort == "none" else -1),
        effort=anthropic_effort,
    )


def build_model(model_name: str) -> AnthropicLlm:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required.")
    return AnthropicLlm(
        model=model_name,
        max_tokens=32768,
        client=AsyncAnthropic(),
    )


def _tool_name(name: str) -> str:
    return name.rsplit("__", 1)[-1]


class TracePlugin(BasePlugin):
    def __init__(self, recorder: TraceRecorder) -> None:
        super().__init__(name="agent-skills-frameworks-trace")
        self.recorder = recorder
        self.tokens = 0
        self.failed_calls: set[tuple[str, str]] = set()

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> None:
        del callback_context
        usage = llm_response.usage_metadata
        if usage is not None:
            self.tokens += int(usage.total_token_count or 0)
        content = llm_response.content
        if content is None:
            return
        for part in content.parts or []:
            if part.text and part.text.strip():
                self.recorder.append("decision", part.text)
            call = part.function_call
            if call is not None:
                name = _tool_name(call.name or "tool")
                call_id = call.id
                self.recorder.append(
                    "tool_call",
                    json.dumps(call.args or {}, ensure_ascii=False),
                    name,
                    call_id,
                )

    async def after_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, result: dict[str, Any]
    ) -> None:
        del tool_args
        name = _tool_name(tool.name)
        call_id = str(getattr(tool_context, "function_call_id", None) or "")
        call = (name, call_id)
        if call in self.failed_calls:
            self.failed_calls.remove(call)
            return
        self.recorder.append(
            "tool_result",
            json.dumps(result, ensure_ascii=False, default=str),
            name,
            call_id or None,
        )

    async def on_tool_error_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, error: Exception
    ) -> dict[str, str]:
        del tool_args
        if not isinstance(error, ToolError):
            raise error
        name = _tool_name(tool.name)
        call_id = str(getattr(tool_context, "function_call_id", None) or "")
        self.failed_calls.add((name, call_id))
        self.recorder.append(
            "tool_result",
            str(error),
            name,
            call_id or None,
        )
        return {"error": str(error)}


async def run_episode(
    case: Case,
    model_name: str,
    effort: str,
    skills: list[dict[str, Any]],
    recorder: TraceRecorder,
    teaching: bool,
    *,
    test_model: BaseLlm | None = None,
) -> EpisodeResult:
    tools = BankingTools(case, skills)
    catalog = [
        {
            "uuid": skill["uuid"],
            "name": skill.get("name", ""),
            "description": skill.get("description", ""),
            "version": skill["version"],
        }
        for skill in skills
    ]
    user_prompt = USER.format(month=case.statement_month, case_id=case.case_id)
    if teaching:
        user_prompt += "\n\n" + TEACHING_REQUEST
    recorder.append("input_reference", user_prompt)
    config = build_config(effort)
    model = test_model or build_model(model_name)
    plugin = TracePlugin(recorder)
    agent = LlmAgent(
        name="banking_agent",
        model=model,
        instruction=SYSTEM.format(catalog=json.dumps(catalog)),
        tools=[FunctionTool(func=getattr(tools, name)) for name in TOOL_NAMES],
        generate_content_config=config,
    )
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name=APP_NAME, user_id="banking-user")
    runner = Runner(
        app=App(name=APP_NAME, root_agent=agent, plugins=[plugin]),
        session_service=sessions,
    )
    final_answer = ""
    message = types.Content(
        role="user",
        parts=[types.Part(text=user_prompt)],
    )
    async with asyncio.timeout(900):
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=message,
        ):
            if event.is_final_response() and event.content:
                final_answer = "".join(
                    part.text or "" for part in event.content.parts or [] if part.text is not None
                )
    if not final_answer.strip():
        raise RuntimeError("The agent returned an empty final answer.")
    return EpisodeResult(final_answer, plugin.tokens, tools)
