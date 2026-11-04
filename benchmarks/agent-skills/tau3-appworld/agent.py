from __future__ import annotations

import hashlib
import json
import time
from contextvars import ContextVar
from typing import Any

import anthropic
from openai import APIError, BadRequestError
from pydantic import BaseModel, Field
from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from call_ledger import record_failure
from episode_audit import EpisodeAudit
from protocol import assert_tools
from redaction import Redactor
from tracing import response_cache_creation_tokens, traced_responses_create
from zep_capture import LOAD_SKILL_TOOL, LOAD_SKILL_TOOL_NAME, SkillToolkit

RETRY_LIMIT = 30
MAX_SKILL_LOAD_TURNS = 3
SUMMARY_UNAVAILABLE_ROUTES: set[tuple[str, str, str]] = set()
CURRENT_EPISODE: ContextVar[dict[str, Any] | None] = ContextVar("CURRENT_EPISODE", default=None)


def hash_prompt_cache_key(composed_key: str) -> str:
    return hashlib.sha256(composed_key.encode()).hexdigest()


def _summary_route_key(client: Any, model: str) -> tuple[str, str, str]:
    responses = client.responses
    underlying = getattr(responses, "_responses", responses)
    sdk_client = getattr(underlying, "_client", client)
    return (
        str(getattr(sdk_client, "base_url", "")),
        model,
        json.dumps(getattr(responses, "_extra_body", {}), sort_keys=True),
    )


class AgentResponseFailedError(RuntimeError):
    """The model provider returned a failed response with no output."""


class AgentContextLimitError(AgentResponseFailedError):
    """The prompt and the requested output are larger than the model context window."""


class AgentResponseIncompleteError(AgentResponseFailedError):
    """The model stopped before the end of its output, for example at the output-token limit."""


class AgentEmptyResponseError(RuntimeError):
    """The model returned a turn with no text and no tool call. This is an agent failure."""


def _raise_on_failed_response(response: Any) -> None:
    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = str(
            getattr(details, "reason", None) or getattr(response, "stop_reason", None) or ""
        )
        record_failure("model", "response.incomplete", reason or "incomplete")
        raise AgentResponseIncompleteError(reason or "incomplete")
    if status != "failed":
        return
    error = getattr(response, "error", None)
    code = str(getattr(error, "code", "") or "")
    message = str(getattr(error, "message", "") or "")
    record_failure("model", "response.failed", code or "failed")
    if "maximum context length" in message:
        raise AgentContextLimitError(f"{code}: {message}")
    raise AgentResponseFailedError(f"{code}: {message}")


class ZepResponsesAgentState(BaseModel):
    input_items: list[dict[str, Any]]
    pending_calls: dict[str, str] = Field(default_factory=dict)
    steps: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    dropped_text_with_tool_call: int = 0


def _item_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return {
        key: value
        for key in ("type", "name", "arguments", "call_id", "role", "content", "id", "text")
        if (value := getattr(item, key, None)) is not None
    }


def _item_type(item: Any) -> str:
    return str(item.get("type", "")) if isinstance(item, dict) else str(getattr(item, "type", ""))


def _responses_tool(tool: Tool) -> dict[str, Any]:
    schema = tool.openai_schema
    function = schema["function"]
    return {
        "type": schema["type"],
        "name": function["name"],
        "description": function["description"],
        "parameters": function["parameters"],
    }


def is_transient_model_error(exc: BaseException) -> bool:
    """Return True for a model error that the episode retry loop sends again.

    OpenAI-compatible and Anthropic clients use the same policy: a 400 is final, and
    connection errors, 5xx, 529, 429, and other status errors are sent again.
    """
    if isinstance(exc, (BadRequestError, anthropic.BadRequestError)):
        return False
    if isinstance(exc, (APIError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        # A stream error event (for example overloaded_error) arrives after an HTTP 200.
        return status >= 500 or status == 429 or status < 400
    return False


def call_model(client: Any, name: str, kwargs: dict[str, Any]) -> Any:
    expected = (CURRENT_EPISODE.get() or {}).get("allowed_tool_names")
    if expected is not None:
        assert_tools(kwargs.get("tools") or [], expected)
    try:
        return traced_responses_create(client, name, kwargs)
    except BadRequestError as exc:
        if "maximum context length" in str(exc):
            raise AgentContextLimitError(str(exc)[:500]) from exc
        raise


@retry(
    stop=stop_after_attempt(RETRY_LIMIT),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception(is_transient_model_error),
    reraise=True,
)
def _completion(client: Any, kwargs: dict[str, Any]) -> Any:
    return call_model(client, "tau2-agent", kwargs)


class ZepResponsesAgent(HalfDuplexAgent[ZepResponsesAgentState]):
    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        *,
        openai_client: Any,
        agent_model: str,
        reasoning_effort: str,
        recorder: Any,
        reasoning_summary: bool = True,
        episode_audit: EpisodeAudit | None = None,
        skills: list[dict[str, Any]] | None = None,
        skill_toolkit: SkillToolkit | None = None,
        redactor: Redactor | None = None,
        prompt_cache_key: str | None = None,
        memory_provider: Any | None = None,
        max_output_tokens: int | None = None,
        usage_totals: dict[str, int] | None = None,
        allowed_tool_names: list[str] | None = None,
    ) -> None:
        super().__init__(tools, domain_policy)
        self.max_output_tokens = max_output_tokens
        self.allowed_tool_names = allowed_tool_names
        self.usage_totals = usage_totals
        self.openai_client = openai_client
        self.agent_model = agent_model
        self.reasoning_effort = reasoning_effort
        self.reasoning_summary = reasoning_summary
        self.episode_audit = episode_audit if episode_audit is not None else EpisodeAudit()
        self.recorder = recorder
        self.skills = skills or []
        self.skill_toolkit = skill_toolkit
        self.redactor = redactor
        self.prompt_cache_key = prompt_cache_key
        self.memory_provider = memory_provider
        self.briefing: str | None = None

    @property
    def _load_tool_name(self) -> str:
        return str(getattr(self.skill_toolkit, "tool_name", LOAD_SKILL_TOOL_NAME))

    @property
    def instructions(self) -> str:
        text = SYSTEM_PROMPT.format(
            agent_instruction=AGENT_INSTRUCTION, domain_policy=self.domain_policy
        )
        if self.skills:
            text += (
                "\n\n## Learned skills\n"
                "The following procedures were compiled from earlier successful runs on this task family.\n"
                "Use a procedure when its preconditions hold. Follow the policy above when they conflict.\n\n"
                + "\n\n".join(str(skill.get("markdown") or "") for skill in self.skills)
            )
        catalog = self.skill_toolkit.catalog() if self.skill_toolkit else ""
        if catalog:
            text += "\n\n" + catalog
        if self.briefing:
            text += (
                "\n\n## Task briefing\n"
                "A curator prepared this briefing from earlier successful runs on similar requests.\n"
                "Follow the policy above when the briefing and the policy disagree. Get every "
                "identifier and value from the user and the tools, not from the briefing.\n\n"
                + self.briefing
            )
        return text

    def get_init_state(self, message_history: list[Any] | None = None) -> ZepResponsesAgentState:
        return ZepResponsesAgentState(input_items=[])

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        emit = getattr(self.recorder, "emit", None)
        if callable(emit):
            emit(kind, data)

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: ZepResponsesAgentState
    ) -> tuple[AssistantMessage, ZepResponsesAgentState]:
        if not self.reasoning_effort:
            raise ValueError("reasoning_effort is required")
        if isinstance(message, UserMessage):
            text = message.content or ""
            state.input_items.append({"role": "user", "content": text})
            self.recorder.record_message("user", text)
            if self.skill_toolkit and self.skill_toolkit.last_search is None:
                query = self.redactor.redact(text) if self.redactor else text
                self.skill_toolkit.search_skills(query, limit=3)
            if self.memory_provider and self.briefing is None:
                query = self.redactor.redact(text) if self.redactor else text
                self.briefing = self.memory_provider.brief(query) or ""
        elif isinstance(message, ToolMessage):
            tool_name = state.pending_calls.pop(message.id, None)
            if tool_name is None:
                raise RuntimeError(f"Unknown tool call id: {message.id}")
            state.input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.id,
                    "output": message.content or "",
                }
            )
            self.recorder.record_tool_result(tool_name, message.content or "", call_id=message.id)
        elif isinstance(message, MultiToolMessage):
            for tool_message in message.tool_messages:
                tool_name = state.pending_calls.pop(tool_message.id, None)
                if tool_name is None:
                    raise RuntimeError(f"Unknown tool call id: {tool_message.id}")
                state.input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_message.id,
                        "output": tool_message.content or "",
                    }
                )
                self.recorder.record_tool_result(
                    tool_name, tool_message.content or "", call_id=tool_message.id
                )

        prompt_tokens = cached_tokens = cache_creation_tokens = completion_tokens = (
            reasoning_tokens
        ) = 0
        output_items: list[Any] = []
        assistant_turn = self.episode_audit.start_turn()
        for turn in range(MAX_SKILL_LOAD_TURNS + 1):
            tools = [_responses_tool(tool) for tool in self.tools]
            if self.skill_toolkit and (
                self.allowed_tool_names is not None
                or (self.skill_toolkit.hits and turn < MAX_SKILL_LOAD_TURNS)
            ):
                tools.append(getattr(self.skill_toolkit, "tool", LOAD_SKILL_TOOL))
            if self.allowed_tool_names is not None:
                assert_tools(tools, self.allowed_tool_names)
            request: dict[str, Any] = {
                "model": self.agent_model,
                "instructions": self.instructions,
                "input": state.input_items,
                "tools": tools,
                "store": False,
                "prompt_cache_key": self.prompt_cache_key,
                "include": ["reasoning.encrypted_content"],
                "reasoning": {"effort": self.reasoning_effort},
            }
            native_thinking = (
                getattr(self.openai_client.responses, "native_thinking", False) is True
            )
            summary_route = _summary_route_key(self.openai_client, self.agent_model)
            summary_unavailable = (
                summary_route in SUMMARY_UNAVAILABLE_ROUTES
                or getattr(self.openai_client.responses, "summary_unavailable", False) is True
            )
            if self.reasoning_summary and not native_thinking and not summary_unavailable:
                request["reasoning"]["summary"] = "auto"
            summary_mode = (
                "provider_native"
                if native_thinking
                else "unsupported"
                if summary_unavailable
                else "requested"
                if self.reasoning_summary
                else "disabled"
            )
            audit_request = self.episode_audit.start_request(assistant_turn, summary_mode)
            if self.max_output_tokens is not None:
                request["max_output_tokens"] = self.max_output_tokens
            started = time.monotonic()
            try:
                try:
                    response = _completion(self.openai_client, request)
                except BadRequestError as exc:
                    if "summary" not in request["reasoning"] or "summary" not in str(exc).lower():
                        raise
                    self.openai_client.responses.summary_unavailable = True
                    SUMMARY_UNAVAILABLE_ROUTES.add(summary_route)
                    audit_request["reasoning_status"] = "unavailable"
                    audit_request["error"] = "summary_option_rejected"
                    request["reasoning"].pop("summary")
                    audit_request = self.episode_audit.start_request(assistant_turn, "unsupported")
                    response = _completion(self.openai_client, request)
            except Exception as exc:
                audit_request["error"] = type(exc).__name__
                raise
            finally:
                if self.usage_totals is not None:
                    self.usage_totals["model_ms"] = self.usage_totals.get("model_ms", 0) + round(
                        1000 * (time.monotonic() - started)
                    )
            output_items = list(getattr(response, "output", []) or [])
            self.episode_audit.record_response(
                assistant_turn, audit_request, [_item_dict(item) for item in output_items]
            )
            _raise_on_failed_response(response)
            state.input_items.extend(_item_dict(item) for item in output_items)
            state.steps += 1
            usage = getattr(response, "usage", None)
            response_usage = {
                "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "cached_tokens": int(
                    getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
                ),
                "cache_creation_tokens": response_cache_creation_tokens(usage),
                "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "reasoning_tokens": int(
                    getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0)
                    or 0
                ),
            }
            prompt_tokens += response_usage["prompt_tokens"]
            cached_tokens += response_usage["cached_tokens"]
            cache_creation_tokens += response_usage["cache_creation_tokens"]
            completion_tokens += response_usage["completion_tokens"]
            reasoning_tokens += response_usage["reasoning_tokens"]
            state.prompt_tokens += response_usage["prompt_tokens"]
            state.cached_tokens += response_usage["cached_tokens"]
            state.cache_creation_tokens += response_usage["cache_creation_tokens"]
            state.completion_tokens += response_usage["completion_tokens"]
            state.reasoning_tokens += response_usage["reasoning_tokens"]
            if self.usage_totals is not None:
                for key, value in response_usage.items():
                    self.usage_totals[key] = self.usage_totals.get(key, 0) + value
            self._emit(
                "usage",
                {
                    "steps": state.steps,
                    "prompt_tokens": state.prompt_tokens,
                    "cached_tokens": state.cached_tokens,
                    "cache_creation_tokens": state.cache_creation_tokens,
                    "completion_tokens": state.completion_tokens,
                    "reasoning_tokens": state.reasoning_tokens,
                },
            )
            if self.reasoning_summary:
                for item in output_items:
                    if _item_type(item) != "reasoning":
                        continue
                    summary = _item_dict(item).get("summary")
                    summaries = summary if isinstance(summary, list) else [summary]
                    for part in summaries:
                        text = (
                            str(part.get("text") or "")
                            if isinstance(part, dict)
                            else str(part or "")
                        )
                        if text:
                            self._emit("reasoning", {"text": text})
            loads = [
                _item_dict(item)
                for item in output_items
                if _item_type(item) == "function_call"
                and _item_dict(item).get("name") == self._load_tool_name
            ]
            if not loads or self.skill_toolkit is None:
                break
            for load in loads:
                arguments = json.loads(str(load.get("arguments") or "{}"))
                name = str(arguments.get(getattr(self.skill_toolkit, "argument", "skill_id"), ""))
                call_id = str(load.get("call_id") or load.get("id") or "")
                try:
                    loaded = self.skill_toolkit.load_skill(name)
                except Exception as exc:
                    self.episode_audit.record_skill_read(
                        call_id,
                        name,
                        {"found": False, "repaired": False, "error": type(exc).__name__},
                    )
                    raise
                read_calls = getattr(self.skill_toolkit, "read_calls", [])
                details = read_calls[-1] if read_calls else {"found": None, "repaired": None}
                if (
                    details.get("found")
                    and name.strip().casefold() != "none"
                    and hasattr(self.skill_toolkit, "resolve")
                ):
                    resolved, _ = self.skill_toolkit.resolve(name)
                    if resolved:
                        details = {
                            **details,
                            "resolved_name": resolved["name"],
                            "skill_id": resolved["uuid"],
                        }
                self.episode_audit.record_skill_read(call_id, name, details)
                state.input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": loaded,
                    }
                )
            if len(loads) < sum(_item_type(item) == "function_call" for item in output_items):
                break
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for item in output_items:
            data = _item_dict(item)
            item_type = _item_type(item)
            if item_type == "function_call" and data.get("name") == self._load_tool_name:
                continue
            if item_type == "function_call":
                call = ToolCall(
                    id=str(data.get("call_id") or data.get("id") or ""),
                    name=str(data.get("name") or ""),
                    arguments=json.loads(str(data.get("arguments") or "{}")),
                    requestor="assistant",
                )
                calls.append(call)
                state.pending_calls[call.id] = call.name
                self.recorder.record_tool_call(call.name, call.arguments, call_id=call.id)
            elif item_type == "message":
                content = data.get("content")
                if isinstance(content, list):
                    text_parts.extend(
                        str(part.get("text", ""))
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "output_text"
                    )
                elif content:
                    text_parts.append(str(content))
            elif item_type == "output_text":
                text_parts.append(str(data.get("text", "")))

        text = "".join(text_parts).strip()
        usage_data = {
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": reasoning_tokens,
        }
        if calls:
            if text:
                state.dropped_text_with_tool_call += 1
            return AssistantMessage(
                role="assistant", content=None, tool_calls=calls, usage=usage_data
            ), state
        if not text:
            raise AgentEmptyResponseError("empty_response")
        self.recorder.record_message("assistant", text)
        return AssistantMessage(role="assistant", content=text, usage=usage_data), state


def create_zep_responses_agent(
    tools: list[Tool], domain_policy: str, **kwargs: Any
) -> ZepResponsesAgent:
    context = CURRENT_EPISODE.get()
    if context is None:
        raise RuntimeError("CURRENT_EPISODE is not set")
    return ZepResponsesAgent(tools, domain_policy, **context)
