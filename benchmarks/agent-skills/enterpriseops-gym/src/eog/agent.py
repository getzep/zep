from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .dataset import Task
from .errors import ContextOverflowError, InfrastructureError
from .prices import prompt_token_count
from .providers.base import ModelTurn, sdk_tool_schema
from .stirrup_constants import (
    BASE_SYSTEM_PROMPT_TEMPLATE,
    CONTEXT_SUMMARIZATION_CUTOFF,
    FINISH_TOOL_DESCRIPTION,
    FINISH_TOOL_NAME,
    FINISH_TOOL_SCHEMA,
    MAX_TURNS,
    MESSAGE_SUMMARIZER,
    MESSAGE_SUMMARIZER_BRIDGE_TEMPLATE,
    MESSAGE_SUMMARIZER_TEXT_ONLY,
    NO_USER_INPUT_GUIDANCE,
    TASK_INSTRUCTIONS_HEADER,
    TURNS_REMAINING_WARNING_THRESHOLD,
    turns_remaining_warning,
)

MODEL_CONTEXT = {
    "claude-opus-5-5": 200_000,
    "claude-fable-5-1": 1_000_000,
    "claude-haiku-5-5": 200_000,
    "gpt-6-luna": 1_050_000,
    "gpt-6.1-sol": 1_050_000,
    "gpt-6-astra": 1_050_000,
    "glm-5.3-flash": 200_000,
    "glm-5.2": 200_000,
    "deepseek-v4.1-flash-1017": 1_048_576,
}
EPISODE_TIMEOUT = 45 * 60
WALL_CLOCK_TIMEOUT = 4 * 60 * 60
MODEL_TIMEOUT = 600
ARMS = ("none", "skills", "skills_decided")
SKILL_ARMS = ("skills", "skills_decided")
INLINE_ARMS = ("skills_decided",)
READ_SKILL_TOOL_NAME = "read_skill"
SKILLS_HEADER = (
    "Reference skills. The list below names skills that other runs used. "
    "Before your first tool call, use the read_skill tool to read the skill whose description matches the task. "
    "Adapt its steps to the task; the skill is a guide, not the task. "
    "When a tool returns an error that a skill description names, read that skill. "
    "The task is only in the user message."
)
SKILLS_HEADER_MULTI_READ = (
    "Reference skills. The list below names skills that other runs used. "
    "Before your first tool call, use the read_skill tool to read each skill whose description "
    "matches a part of the task. "
    "A task can need more than one skill. "
    "Adapt its steps to the task; the skill is a guide, not the task. "
    "When a tool returns an error that a skill description names, read that skill. "
    "The task is only in the user message."
)
SKILLS_DECIDED_HEADER = (
    "Reference skill. The skill below was selected for this task. "
    "Read it before your first tool call. Adapt its steps to the task; the skill is a guide, not the task. "
    "The task is only in the user message."
)
READ_SKILL_NUDGE = "A reference skill may match this task. Read it with read_skill, or continue."
READ_SKILL_NAME_DISTANCE = 3
READ_SKILL_NONE = "none"
SHORT_ID_LENGTH = 8
SKILLS_HEADER_DECLINE = (
    f'If no skill matches the task, call read_skill with the name "{READ_SKILL_NONE}" and continue with the tools.'
)
READ_SKILL_NONE_TEXT = "No skill selected. Continue the task with the tools."
READ_SKILL_TOOL = {
    "name": READ_SKILL_TOOL_NAME,
    "description": "Read one reference skill from the Reference skills list. The result is the skill text.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the skill, as the Reference skills list gives it.",
            }
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}


def read_skill_tool(index: SkillIndex | None = None, *, enum: bool = False) -> dict[str, Any]:
    """The read_skill tool.

    With an index of short ids, the name argument is described as the short id. With enum, the name
    argument is an enum of the index handles plus "none".
    """
    tool = copy.deepcopy(READ_SKILL_TOOL)
    if index is None or not index.entries:
        return tool
    name_schema = tool["inputSchema"]["properties"]["name"]
    what = "The short id of the skill (8 characters)" if index.short_ids else "The name of the skill"
    if index.short_ids:
        name_schema["description"] = f"{what}, as the Reference skills list gives it."
    if enum:
        name_schema["enum"] = [*(index.handle(entry) for entry in index.entries), READ_SKILL_NONE]
        name_schema["description"] = (
            f'{what}, as the Reference skills list gives it, or "{READ_SKILL_NONE}" when no skill matches the task.'
        )
    return tool


@dataclass(frozen=True, slots=True)
class SkillIndexEntry:
    id: str
    sha256: str
    name: str
    description: str
    markdown: str


@dataclass(frozen=True, slots=True)
class SkillIndex:
    entries: tuple[SkillIndexEntry, ...]
    short_ids: bool = False

    def handle(self, entry: SkillIndexEntry) -> str:
        """The handle of an entry in the index and in read_skill: the first 8 hex characters of the Skill UUID
        with short ids, else the Skill name."""
        if not self.short_ids:
            return entry.name
        return uuid.UUID(entry.id).hex[:SHORT_ID_LENGTH]

    def lines(self) -> str:
        return "\n".join(f"- {self.handle(entry)}: {entry.description}" for entry in self.entries)

    def block(self, *, decline: bool = False, multi_read: bool = False) -> str:
        """The header and the index lines; multi_read selects every matching skill."""
        if not self.entries:
            return ""
        header = SKILLS_HEADER_MULTI_READ if multi_read else SKILLS_HEADER
        if decline:
            header = f"{header} {SKILLS_HEADER_DECLINE}"
        return f"{header}\n\n{self.lines()}"

    def resolve(self, name: object) -> tuple[SkillIndexEntry | None, str | None]:
        """The entry of a requested name and, when the name needed a repair, the raw requested name.

        The match is the trimmed name, then the same name in any letter case, then the nearest index name
        within an edit distance of READ_SKILL_NAME_DISTANCE (ties go to the index order).
        """
        if not isinstance(name, str):
            return None, None
        wanted = name.strip()
        if self.short_ids:
            folded_handle = wanted.replace("-", "").lower()
            by_handle = next((entry for entry in self.entries if self.handle(entry) == folded_handle), None)
            if by_handle is not None:
                return by_handle, None
        exact = next((entry for entry in self.entries if entry.name == wanted), None)
        if exact is not None:
            return exact, None
        folded = wanted.casefold()
        nearest: SkillIndexEntry | None = None
        best = READ_SKILL_NAME_DISTANCE + 1
        for entry in self.entries:
            distance = _edit_distance(folded, entry.name.casefold())
            if distance < best:
                nearest, best = entry, distance
        if nearest is None:
            return None, None
        return nearest, name

    def lookup(self, name: object) -> SkillIndexEntry | None:
        return self.resolve(name)[0]

    def metadata(self) -> list[dict[str, str]]:
        return [
            {"id": entry.id, "sha256": entry.sha256, "name": entry.name, "handle": self.handle(entry)}
            for entry in self.entries
        ]


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > READ_SKILL_NAME_DISTANCE:
        return READ_SKILL_NAME_DISTANCE + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def selected_skills_block(bodies: list[str] | tuple[str, ...]) -> str:
    if not bodies:
        return ""
    return f"{SKILLS_DECIDED_HEADER}\n\n" + "\n\n".join(bodies)


class SkillReader:
    """Answers read_skill calls from a frozen Skill index. It has the call_tool interface of an MCP client."""

    def __init__(self, index: SkillIndex) -> None:
        self.index = index

    def call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        if name != READ_SKILL_TOOL_NAME:
            return {"content": [{"type": "text", "text": f"{name} is not a valid tool"}], "isError": True}
        requested = arguments.get("name") if isinstance(arguments, dict) else None
        entry = self.index.lookup(requested)
        if entry is None and isinstance(requested, str) and requested.strip().lower() == READ_SKILL_NONE:
            return {"content": [{"type": "text", "text": READ_SKILL_NONE_TEXT}], "isError": False}
        if entry is None:
            text = f'No skill is named "{requested}". The Reference skills are:\n{self.index.lines()}'
            return {"content": [{"type": "text", "text": text}], "isError": True}
        return {"content": [{"type": "text", "text": entry.markdown}], "isError": False}


@dataclass(frozen=True, slots=True)
class AgentConfig:
    system: str
    tools: tuple[dict[str, Any], ...]
    max_turns: int
    turns_remaining_warning_threshold: int
    context_summarization_cutoff: float
    model_timeout: int
    episode_timeout: int
    arm: str


@dataclass(frozen=True, slots=True)
class AgentResult:
    finish_reason: str
    turns: int
    history: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    usage: dict[str, int | None]
    active_seconds: float = 0.0
    final_text: str = ""
    read_skill_calls: tuple[dict[str, Any], ...] = ()
    nudged: bool = False
    user_index_chars: int = 0
    forced_first_read: bool = False


def finish_tool() -> dict[str, Any]:
    return {
        "name": FINISH_TOOL_NAME,
        "description": FINISH_TOOL_DESCRIPTION,
        "inputSchema": FINISH_TOOL_SCHEMA,
    }


def build_agent_config(
    task: Task,
    available_tools: list[dict[str, Any]],
    *,
    arm: str = "none",
    skills_block: str | None = None,
    selected_tools: tuple[str, ...] | None = None,
    skill_index: SkillIndex | None = None,
    skill_name_enum: bool = False,
    skills_multi_read: bool = False,
    system_preamble: str | None = None,
) -> AgentConfig:
    if arm not in ARMS:
        raise ValueError(f"Unknown arm: {arm}")
    if arm == "none" and (skills_block or (skill_index and skill_index.entries)):
        raise ValueError("The no-Skills arm cannot include a Skills block or a Skill index")
    if arm in INLINE_ARMS and skill_index and skill_index.entries:
        raise ValueError(f"The {arm} arm cannot include a Skill index")
    system = (
        f"{BASE_SYSTEM_PROMPT_TEMPLATE.format(max_turns=MAX_TURNS)}"
        f"{NO_USER_INPUT_GUIDANCE}{TASK_INSTRUCTIONS_HEADER}{task.system_prompt}"
    )
    if system_preamble is not None:
        system = f"{system_preamble}\n\n{system}"
    extra_tools: list[dict[str, Any]] = []
    if arm == "skills" and skill_index is not None and skill_index.entries:
        if skills_block is not None and skills_block != skill_index.block(
            decline=skill_name_enum,
            multi_read=skills_multi_read,
        ):
            raise ValueError("The skills arm block must equal the Skill index block")
        system = f"{system}\n\n{skill_index.block(decline=skill_name_enum, multi_read=skills_multi_read)}"
        extra_tools.append(read_skill_tool(skill_index, enum=skill_name_enum))
    elif arm in INLINE_ARMS and skills_block:
        system = f"{system}\n\n{skills_block}"
    filtered = {tool["name"]: tool for tool in available_tools}
    selected = []
    for name in selected_tools if selected_tools is not None else task.selected_tools:
        if name not in filtered:
            raise InfrastructureError(f"Selected MCP tool is missing: {name}")
        selected.append(filtered[name])
    return AgentConfig(
        system=system,
        tools=tuple([finish_tool(), *extra_tools, *selected]),
        max_turns=MAX_TURNS,
        turns_remaining_warning_threshold=TURNS_REMAINING_WARNING_THRESHOLD,
        context_summarization_cutoff=CONTEXT_SUMMARIZATION_CUTOFF,
        model_timeout=MODEL_TIMEOUT,
        episode_timeout=EPISODE_TIMEOUT,
        arm=arm,
    )


def _tool_results(
    turn: ModelTurn,
    mcp_client: Any,
    clock: Any,
    latency_sink: list[float] | None = None,
    *,
    skill_reader: Any | None = None,
    turn_index: int = 0,
    read_skill_calls: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool, str, list[float]]:
    messages = []
    latencies = []
    finished = False
    reason = ""
    for call in turn.tool_calls:
        if call.name == READ_SKILL_TOOL_NAME and skill_reader is not None:
            result = skill_reader.call_tool(call.name, call.arguments)
            requested = call.arguments.get("name") if isinstance(call.arguments, dict) else None
            if read_skill_calls is not None:
                index = getattr(skill_reader, "index", None)
                entry, repaired_from = index.resolve(requested) if index is not None else (None, None)
                read_skill_calls.append(
                    {
                        "name": requested if isinstance(requested, str) else None,
                        "turn": turn_index,
                        "found": not bool(result.get("isError", False)) and entry is not None,
                        "resolved_name": entry.name if entry is not None else None,
                        "repaired_from": repaired_from,
                    }
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": "\n".join(
                        str(block["text"])
                        for block in result.get("content", [])
                        if isinstance(block, dict) and block.get("type") == "text"
                    ),
                    "is_error": bool(result.get("isError", False)),
                }
            )
            continue
        if not isinstance(call.arguments, dict):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": "Error: Invalid parameters",
                    "is_error": True,
                }
            )
            continue
        if call.name == FINISH_TOOL_NAME:
            finished = True
            reason = str(call.arguments.get("reason", ""))
            continue
        started = clock()
        try:
            result = mcp_client.call_tool(call.name, call.arguments)
        finally:
            latency = clock() - started
            latencies.append(latency)
            if latency_sink is not None:
                latency_sink.append(latency)
        content = "\n".join(
            str(block["text"])
            for block in result.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": content,
                "is_error": bool(result.get("isError", False)),
            }
        )
    return messages, finished, reason, latencies


def _summarize(
    route: Any,
    system: str,
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    effort: str,
    task_id: str,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[float]]:
    first_turn = next(
        (index for index, message in enumerate(history) if message.get("role") == "assistant"),
        len(history),
    )
    task_context = history[:first_turn]
    if first_turn == len(history):
        return history, []
    text_only_prompt = f"{MESSAGE_SUMMARIZER}\n\n{MESSAGE_SUMMARIZER_TEXT_ONLY}"
    tool_docs = "\n".join(f"- {tool['name']}: {tool.get('description', '')}" for tool in tools)
    no_tools_prompt = (
        f"{text_only_prompt}\n\nTools are disabled for this response. "
        f"For reference, the tools available earlier in the conversation were:\n{tool_docs}"
    )
    summary_text = ""
    latencies = []
    for prompt, active_tools in (
        (MESSAGE_SUMMARIZER, tools),
        (text_only_prompt, tools),
        (no_tools_prompt, []),
    ):
        summary_turn = route.complete(
            system,
            [*history, {"role": "user", "content": prompt}],
            active_tools,
            effort=effort,
            task_id=task_id,
            context=context,
        )
        latencies.append(summary_turn.latency_seconds)
        summary_text = summary_turn.text.strip()
        if summary_text:
            break
    if not summary_text:
        raise InfrastructureError("Stirrup context summarization returned no text")
    bridge = MESSAGE_SUMMARIZER_BRIDGE_TEMPLATE.format(summary=summary_text)
    return (
        [*task_context, {"role": "assistant", "content": bridge}, {"role": "user", "content": "Got it, thanks!"}],
        latencies,
    )


def run_agent(
    task: Task,
    route: Any,
    mcp_client: Any,
    available_tools: list[dict[str, Any]],
    *,
    effort: str,
    arm: str = "none",
    skills_block: str | None = None,
    selected_tools: tuple[str, ...] | None = None,
    context: dict[str, Any] | None = None,
    clock: Any = time.monotonic,
    skill_reader: Any | None = None,
    force_first_read: bool = False,
    skill_name_enum: bool = False,
    skills_multi_read: bool = False,
    system_preamble: str | None = None,
) -> AgentResult:
    skill_index = getattr(skill_reader, "index", None) if skill_reader is not None else None
    config = build_agent_config(
        task,
        available_tools,
        arm=arm,
        skills_block=skills_block,
        selected_tools=selected_tools,
        skill_index=skill_index,
        skill_name_enum=skill_name_enum,
        skills_multi_read=skills_multi_read,
        system_preamble=system_preamble,
    )
    if arm != "skills":
        skill_reader = None
    read_skill_calls: list[dict[str, Any]] = []
    start = clock()
    active_seconds = 0.0
    tool_call_latencies = context.get("tool_call_latencies") if context else None
    index_active = bool(arm == "skills" and skill_index is not None and skill_index.entries)
    history: list[dict[str, Any]] = [{"role": "user", "content": task.user_prompt}]
    events: list[dict[str, Any]] = [{"type": "observation", "content": task.user_prompt}]
    nudged = False
    totals = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    finish_reason = "turn_limit"
    final_text = ""
    for turn_index in range(1, config.max_turns + 1):
        if turn_index > 1:
            remaining = config.max_turns - turn_index + 1
            if 0 < remaining <= config.turns_remaining_warning_threshold:
                history.append({"role": "user", "content": turns_remaining_warning(remaining)})
        if clock() - start >= WALL_CLOCK_TIMEOUT:
            raise InfrastructureError(f"Episode timed out for {task.task_id}")
        forced = bool(
            force_first_read
            and index_active
            and turn_index == 1
            and getattr(route, "supports_forced_tool_choice", True)
        )
        call_kwargs: dict[str, Any] = {"effort": effort, "task_id": task.task_id, "context": context}
        if forced:
            call_kwargs["tool_choice"] = READ_SKILL_TOOL_NAME
        try:
            turn = route.complete(config.system, history, list(config.tools), **call_kwargs)
        except ContextOverflowError:
            finish_reason = "context_overflow"
            events.append({"type": "context_overflow", "turn": turn_index})
            break
        if turn.text:
            final_text = turn.text
        active_seconds += turn.latency_seconds
        if clock() - start >= WALL_CLOCK_TIMEOUT:
            raise InfrastructureError(f"Episode wall-clock limit reached for {task.task_id}")
        for name in totals:
            value = getattr(turn.usage, name)
            if value is None:
                totals[name] = None
            elif totals[name] is not None:
                totals[name] += value
        events.append({"type": "decision", "content": turn.text})
        history.extend(turn.history_items or ({"role": "assistant", "content": turn.text},))
        if active_seconds >= config.episode_timeout:
            events.append(
                {
                    "type": "turn",
                    "turn": turn_index,
                    "tool_names": [],
                    "model_latency_seconds": turn.latency_seconds,
                    "tool_latencies_seconds": [],
                    "finished": False,
                    "usage": {name: getattr(turn.usage, name) for name in totals},
                }
            )
            finish_reason = "active_time_limit"
            break
        tool_messages, finished, reason, tool_latencies = _tool_results(
            turn,
            mcp_client,
            clock,
            latency_sink=tool_call_latencies,
            skill_reader=skill_reader,
            turn_index=turn_index,
            read_skill_calls=read_skill_calls,
        )
        for call in turn.tool_calls:
            events.append(
                {
                    "type": "tool_call",
                    "content": json.dumps(call.arguments, sort_keys=True),
                    "tool": call.name,
                    "tool_call_id": call.id,
                }
            )
        for message in tool_messages:
            call = next((item for item in turn.tool_calls if item.id == message["tool_call_id"]), None)
            events.append(
                {
                    "type": "tool_result",
                    "content": message["content"],
                    "tool": call.name if call else "",
                    "tool_call_id": message["tool_call_id"],
                    "is_error": bool(message.get("is_error", False)),
                }
            )
        active_seconds += sum(tool_latencies)
        if clock() - start >= WALL_CLOCK_TIMEOUT:
            raise InfrastructureError(f"Episode wall-clock limit reached for {task.task_id}")
        history.extend(tool_messages)
        events.append(
            {
                "type": "turn",
                "turn": turn_index,
                "tool_names": [call.name for call in turn.tool_calls],
                "model_latency_seconds": turn.latency_seconds,
                "tool_latencies_seconds": tool_latencies,
                "finished": finished,
                "usage": {name: getattr(turn.usage, name) for name in totals},
            }
        )
        if active_seconds >= config.episode_timeout:
            finish_reason = "active_time_limit"
            break
        if finished:
            finish_reason = "finished"
            final_text = reason or turn.text or final_text
            break
        if (
            turn_index == 1
            and index_active
            and not force_first_read
            and turn.tool_calls
            and all(call.name != READ_SKILL_TOOL_NAME for call in turn.tool_calls)
        ):
            history.append({"role": "user", "content": READ_SKILL_NUDGE})
            events.append({"type": "read_skill_nudge", "turn": turn_index})
            nudged = True
        remaining = config.max_turns - turn_index
        warning_next_turn = 0 < remaining <= config.turns_remaining_warning_threshold
        if not turn.tool_calls and turn.text and not warning_next_turn:
            history.append({"role": "user", "content": "Please continue the task"})
        context_window = MODEL_CONTEXT.get(route.model, 200_000)
        tokens_used = prompt_token_count(
            route.model,
            input_tokens=turn.usage.input,
            cache_read_tokens=turn.usage.cache_read,
            cache_write_tokens=turn.usage.cache_write,
        )
        if tokens_used / context_window >= config.context_summarization_cutoff and turn_index != config.max_turns:
            try:
                history, summary_latencies = _summarize(
                    route, config.system, history, list(config.tools), effort, task.task_id, context
                )
            except ContextOverflowError:
                finish_reason = "context_overflow"
                events.append({"type": "context_overflow", "turn": turn_index})
                break
            active_seconds += sum(summary_latencies)
            events.append(
                {
                    "type": "context_summarization",
                    "turn": turn_index,
                    "tokens": tokens_used,
                    "model_latencies_seconds": summary_latencies,
                }
            )
            if clock() - start >= WALL_CLOCK_TIMEOUT:
                raise InfrastructureError(f"Episode wall-clock limit reached for {task.task_id}")
            if active_seconds >= config.episode_timeout:
                finish_reason = "active_time_limit"
                break
    else:
        events.append({"type": "turn_limit", "turn": config.max_turns})
    return AgentResult(
        finish_reason,
        turn_index,
        tuple(history),
        tuple(events),
        totals,
        active_seconds,
        final_text,
        tuple(read_skill_calls),
        nudged,
        0,
        bool(force_first_read and index_active and getattr(route, "supports_forced_tool_choice", True)),
    )


def tool_schemas(config: AgentConfig) -> list[dict[str, Any]]:
    return sdk_tool_schema(list(config.tools))
