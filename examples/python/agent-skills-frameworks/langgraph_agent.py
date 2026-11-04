"""LangGraph adapter for the shared invented banking tools."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langchain_openai import ChatOpenAI

from banking import Case, PackageSelection
from prompts import SYSTEM, TEACHING_REQUEST, USER
from tools import BankingTools, ToolError
from zep_skills import TraceRecorder

REQUEST_TIMEOUT = 600
MAX_OUTPUT_TOKENS = 32768


class SerialChatAnthropic(ChatAnthropic):
    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: dict[str, str] | str | None = None,
        parallel_tool_calls: bool | None = None,
        strict: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        return super().bind_tools(
            tools,
            tool_choice=tool_choice,
            parallel_tool_calls=False,
            strict=strict,
            **kwargs,
        )


class SerialChatOpenAI(ChatOpenAI):
    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: dict[str, Any] | str | bool | None = None,
        parallel_tool_calls: bool | None = None,
        strict: bool | None = None,
        response_format: Any = None,
        **kwargs: Any,
    ) -> Any:
        return super().bind_tools(
            tools,
            tool_choice=tool_choice,
            parallel_tool_calls=False,
            strict=strict,
            response_format=response_format,
            **kwargs,
        )


def model_identifier(model_name: str) -> tuple[str, str]:
    provider, separator, name = model_name.partition(":")
    if not separator:
        provider = ""
        name = model_name
        if name.startswith("claude-"):
            provider = "anthropic"
        elif name.startswith(("gpt-", "o1", "o3", "o4")):
            provider = "openai"
    if provider not in {"anthropic", "openai"} or not name:
        raise ValueError("Use an anthropic:MODEL or openai:MODEL identifier.")
    return provider, name


def build_model(model_name: str, effort: str) -> BaseChatModel:
    provider, name = model_identifier(model_name)
    if provider == "anthropic":
        if effort not in {"none", "low", "max"}:
            raise ValueError("Anthropic effort must be none, low, or max.")
        anthropic_effort: Literal["low", "max"] | None = (
            None if effort == "none" else cast(Literal["low", "max"], effort)
        )
        return SerialChatAnthropic(
            model_name=name,
            max_tokens_to_sample=MAX_OUTPUT_TOKENS,
            stop=None,
            timeout=REQUEST_TIMEOUT,
            max_retries=0,
            thinking={"type": "disabled"} if effort == "none" else {"type": "adaptive"},
            effort=anthropic_effort,
        )
    return SerialChatOpenAI(
        model=name,
        max_completion_tokens=MAX_OUTPUT_TOKENS,
        timeout=REQUEST_TIMEOUT,
        max_retries=0,
        reasoning_effort=None if effort == "none" else effort,
    )


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


@dataclass
class EpisodeResult:
    final_answer: str
    tokens: int
    tools: BankingTools


class TraceCallbacks(BaseCallbackHandler):
    raise_error = True

    def __init__(self, recorder: TraceRecorder) -> None:
        self.recorder = recorder
        self.pending_calls: deque[tuple[str, str, str]] = deque()
        self.tool_runs: dict[UUID, tuple[str, str | None]] = {}
        self.tokens = 0

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del run_id, parent_run_id, tags, kwargs
        for group in response.generations:
            for generation in group:
                if not isinstance(generation, ChatGeneration):
                    continue
                message = generation.message
                if not isinstance(message, AIMessage):
                    continue
                usage = message.usage_metadata or {}
                self.tokens += int(usage.get("total_tokens", 0))
                text = _text_content(message.content)
                if text.strip():
                    self.recorder.append("decision", text)
                if len(message.tool_calls) > 1:
                    raise RuntimeError("The model returned parallel tool calls.")
                for call in message.tool_calls:
                    name = str(call["name"])
                    call_id = str(call["id"])
                    arguments = json.dumps(call["args"], ensure_ascii=False)
                    self.recorder.append("tool_call", arguments, name, call_id)
                    self.pending_calls.append((name, call_id, arguments))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del input_str, parent_run_id, tags, metadata
        name = str(serialized.get("name", "tool"))
        call_id = str((inputs or {}).get("tool_call_id") or kwargs.get("tool_call_id") or "")
        if self.pending_calls:
            pending_name, pending_id, _ = self.pending_calls.popleft()
            if pending_name == name:
                call_id = call_id or pending_id
        self.tool_runs[run_id] = (name, call_id or None)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, kwargs
        name, call_id = self.tool_runs.pop(run_id, ("tool", None))
        result = output.content if isinstance(output, ToolMessage) else output
        self.recorder.append(
            "tool_result",
            json.dumps(result, ensure_ascii=False, default=str),
            name,
            call_id,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, kwargs
        name, call_id = self.tool_runs.pop(run_id, ("tool", None))
        self.recorder.append("tool_result", str(error), name, call_id)


def _adapt_tools(tools: BankingTools) -> list[BaseTool]:
    def call_tool(function: Any, tool_call_id: str, *args: Any, **kwargs: Any) -> Any | ToolMessage:
        try:
            return function(*args, **kwargs)
        except ToolError as error:
            return ToolMessage(
                content=str(error),
                tool_call_id=tool_call_id,
                status="error",
            )

    @tool
    def get_case() -> dict[str, Any]:
        """Read the case's savings, checking, cards, and interest ledger for all months."""
        return tools.get_case()

    @tool
    def list_documents() -> list[dict[str, str]]:
        """List the titles and IDs of all available bank policy documents."""
        return tools.list_documents()

    @tool
    def read_document(
        document_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, str] | ToolMessage:
        """Read the complete bank policy document with this ID."""
        return call_tool(tools.read_document, tool_call_id, document_id)

    @tool
    def calculator(
        expression: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str | ToolMessage:
        """Evaluate +, -, *, / and parentheses. Results round half-up to cents: use the full formula in one call."""
        return call_tool(tools.calculator, tool_call_id, expression)

    @tool
    def quote_interest(
        account_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        checking_account_id: str | None = None,
        card_id: str | None = None,
    ) -> dict[str, Any] | ToolMessage:
        """Quote statement interest for one proposed checking/card package. This does not post a credit."""
        return call_tool(
            tools.quote_interest, tool_call_id, account_id, checking_account_id, card_id
        )

    @tool
    def compare_packages(packages: list[PackageSelection]) -> dict[str, Any]:
        """Compare submitted packages and retain the best complete allocation quoted so far. No omitted packages or unions of separate batches are added. The first saved credit locks the allocation."""
        return tools.compare_packages(packages)

    @tool
    def credit_interest(
        account_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, Any] | ToolMessage:
        """Post one correction from this account's package in the best quoted allocation. The bank derives the amount. The first saved credit locks the allocation."""
        return call_tool(tools.credit_interest, tool_call_id, account_id)

    @tool
    def file_discrepancy_report(
        account_id: str,
        correction_credit_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, Any] | ToolMessage:
        """Save this account's report from the locked allocation and its saved correction credit. The bank derives the report fields."""
        return call_tool(
            tools.file_discrepancy_report, tool_call_id, account_id, correction_credit_id
        )

    @tool
    def read_skill(
        skill_uuid: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, Any] | ToolMessage:
        """Read the complete Markdown of a Skill from the supplied live search catalog."""
        return call_tool(tools.read_skill, tool_call_id, skill_uuid)

    return cast(
        "list[BaseTool]",
        [
            get_case,
            list_documents,
            read_document,
            calculator,
            quote_interest,
            compare_packages,
            credit_interest,
            file_discrepancy_report,
            read_skill,
        ],
    )


async def run_episode(
    case: Case,
    model_name: str,
    effort: str,
    skills: list[dict[str, Any]],
    recorder: TraceRecorder,
    teaching: bool = False,
    *,
    test_model: BaseChatModel | None = None,
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
    model = test_model or build_model(model_name, effort)
    agent = create_agent(
        model,
        tools=_adapt_tools(tools),
        system_prompt=SYSTEM.format(catalog=json.dumps(catalog)),
    )
    callbacks = TraceCallbacks(recorder)
    async with asyncio.timeout(900):
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_prompt}]},
            config={"callbacks": [callbacks], "recursion_limit": 302},
        )
    messages = result["messages"]
    final_answer = _text_content(messages[-1].content) if messages else ""
    if not final_answer.strip():
        raise RuntimeError("The agent returned an empty final answer.")
    return EpisodeResult(final_answer, callbacks.tokens, tools)
