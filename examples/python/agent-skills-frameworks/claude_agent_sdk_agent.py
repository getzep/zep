"""Claude Agent SDK adapter for the shared invented banking tools."""

from __future__ import annotations

import asyncio
import json
import tempfile
from inspect import Parameter, signature
from typing import Any, Literal, cast, get_type_hints

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from pydantic import BaseModel, ValidationError, create_model

from banking import Case
from episode import EpisodeResult
from prompts import SYSTEM, TEACHING_REQUEST, USER
from tools import BankingTools, ToolError
from zep_skills import TraceRecorder

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
EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}


def _bare_tool_name(name: str) -> str:
    return name.removeprefix("mcp__banking__")


def _argument_model(method: Any) -> type[BaseModel]:
    hints = get_type_hints(method)
    fields: dict[str, Any] = {}
    for name, parameter in signature(method).parameters.items():
        default = ... if parameter.default is Parameter.empty else parameter.default
        fields[name] = (hints.get(name, Any), default)
    return create_model(f"{method.__name__}Arguments", **cast(Any, fields))


def _tool_result(value: Any) -> str:
    if isinstance(value, dict) and value.get("is_error"):
        content = value.get("content", [])
        if isinstance(content, list):
            return "\n".join(
                str(item["text"]) for item in content if isinstance(item, dict) and "text" in item
            )
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


def record_assistant_text(recorder: TraceRecorder, text: str) -> None:
    if text.strip():
        recorder.append("decision", text)


def build_tools(tools: BankingTools) -> list[Any]:
    sdk_tools = []
    for name in TOOL_NAMES:
        method = getattr(tools, name)
        argument_model = _argument_model(method)

        async def handler(
            args: dict[str, Any],
            method: Any = method,
            argument_model: type[BaseModel] = argument_model,
        ) -> dict[str, Any]:
            try:
                validated = argument_model.model_validate(args)
                validated_args = {
                    field: getattr(validated, field) for field in argument_model.model_fields
                }
                result = method(**validated_args)
            except ToolError as error:
                return {"content": [{"type": "text", "text": str(error)}], "is_error": True}
            except ValidationError as error:
                return {"content": [{"type": "text", "text": str(error)}], "is_error": True}
            return {"content": [{"type": "text", "text": _tool_result(result)}]}

        sdk_tools.append(
            tool(name, method.__doc__ or name, argument_model.model_json_schema())(handler)
        )
    return sdk_tools


class TraceHooks:
    def __init__(self, recorder: TraceRecorder) -> None:
        self.recorder = recorder
        self.recorded_tool_results: set[str] = set()

    async def pre_tool_use(self, input_data: Any, *_: Any) -> Any:
        name = _bare_tool_name(str(input_data["tool_name"]))
        self.recorder.append(
            "tool_call",
            json.dumps(input_data.get("tool_input", {}), ensure_ascii=False),
            name,
            input_data.get("tool_use_id"),
        )
        return {"continue": True}

    async def post_tool_use(self, input_data: Any, *_: Any) -> Any:
        call_id = str(input_data.get("tool_use_id", ""))
        if call_id in self.recorded_tool_results:
            return {}
        self.recorded_tool_results.add(call_id)
        self.recorder.append(
            "tool_result",
            _tool_result(input_data.get("tool_response")),
            _bare_tool_name(str(input_data["tool_name"])),
            call_id or None,
        )
        return {}

    async def post_tool_use_failure(self, input_data: Any, *_: Any) -> Any:
        call_id = str(input_data.get("tool_use_id", ""))
        if call_id in self.recorded_tool_results:
            return {}
        self.recorded_tool_results.add(call_id)
        self.recorder.append(
            "tool_result",
            str(input_data.get("error", "Tool failed.")),
            _bare_tool_name(str(input_data["tool_name"])),
            call_id or None,
        )
        return {}


async def run_episode(
    case: Case,
    model_name: str,
    effort: str,
    skills: list[dict[str, Any]],
    recorder: TraceRecorder,
    teaching: bool,
    *,
    test_model: Any | None = None,
) -> EpisodeResult:
    del test_model
    if effort not in EFFORTS:
        raise ValueError(f"Effort must be one of {', '.join(sorted(EFFORTS))}.")
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
    hooks = TraceHooks(recorder)
    server = create_sdk_mcp_server(name="banking", tools=build_tools(tools))
    allowed = [f"mcp__banking__{name}" for name in TOOL_NAMES]
    options = ClaudeAgentOptions(
        tools=[],
        allowed_tools=allowed,
        setting_sources=[],
        permission_mode="bypassPermissions",
        system_prompt=SYSTEM.format(catalog=json.dumps(catalog)),
        model=model_name,
        effort=(
            None
            if effort == "none"
            else cast(Literal["low", "medium", "high", "xhigh", "max"], effort)
        ),
        thinking={"type": "disabled"} if effort == "none" else {"type": "adaptive"},
        mcp_servers={"banking": server},
        hooks={
            "PreToolUse": [HookMatcher(hooks=[hooks.pre_tool_use])],
            "PostToolUse": [HookMatcher(hooks=[hooks.post_tool_use])],
            "PostToolUseFailure": [HookMatcher(hooks=[hooks.post_tool_use_failure])],
        },
    )
    last_assistant_text = ""
    result_answer = ""
    tokens = 0
    with tempfile.TemporaryDirectory(prefix="agent-skills-frameworks-") as cwd:
        options.cwd = cwd
        async with asyncio.timeout(900):
            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    message_text = []
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            message_text.append(block.text)
                            record_assistant_text(recorder, block.text)
                    last_assistant_text = "".join(message_text)
                elif isinstance(message, ResultMessage):
                    if message.usage:
                        tokens += sum(
                            int(message.usage.get(field, 0))
                            for field in (
                                "input_tokens",
                                "output_tokens",
                                "cache_read_input_tokens",
                                "cache_creation_input_tokens",
                            )
                        )
                    result_answer = message.result or ""
                    if message.is_error:
                        detail = ", ".join(message.errors or []) or message.subtype
                        raise RuntimeError(f"Claude Agent SDK returned an error: {detail}")
    final_answer = result_answer or last_assistant_text
    if not final_answer.strip():
        raise RuntimeError("The agent returned an empty final answer.")
    return EpisodeResult(final_answer, tokens, tools)
