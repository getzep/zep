from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from eog.agent import (
    READ_SKILL_NONE_TEXT,
    READ_SKILL_NUDGE,
    READ_SKILL_TOOL,
    SKILLS_DECIDED_HEADER,
    SKILLS_HEADER,
    SKILLS_HEADER_DECLINE,
    SKILLS_HEADER_MULTI_READ,
    SkillIndex,
    SkillReader,
    build_agent_config,
    run_agent,
    selected_skills_block,
)
from eog.dataset import load_tasks
from eog.providers.base import ModelTurn, ToolCall, Usage
from eog.skills import build_skill_index, skill_frontmatter
from eog.stirrup_constants import (
    BASE_SYSTEM_PROMPT_TEMPLATE,
    MAX_TURNS,
    NO_USER_INPUT_GUIDANCE,
    TASK_INSTRUCTIONS_HEADER,
)

SKILL_IDS = ("skill-a", "skill-b", "skill-c")


def _markdown(index: int) -> str:
    return (
        f"---\nname: agent-skill-{index}\n"
        f'description: "Procedure {index} with a \\"quoted\\" word."\n---\n\n'
        f"# Procedure {index}\n\n1. Step one of procedure {index}.\n"
    )


def _snapshot(family: str) -> dict[str, dict[str, str]]:
    return {
        skill_id: {
            "id": skill_id,
            "sha256": hashlib.sha256(_markdown(index).encode()).hexdigest(),
            "markdown": _markdown(index),
            "task_family": family,
        }
        for index, skill_id in enumerate(SKILL_IDS, start=1)
    }


def _task():
    return next(task for task in load_tasks() if task.domain == "calendar")


def _tools(task) -> list[dict[str, Any]]:
    return [{"name": name, "inputSchema": {"type": "object"}} for name in task.selected_tools]


def test_frontmatter_reads_name_and_unescapes_description() -> None:
    fields = skill_frontmatter(_markdown(1))
    assert fields == {"name": "agent-skill-1", "description": 'Procedure 1 with a "quoted" word.'}
    assert skill_frontmatter("# No frontmatter\n") == {}


def test_index_block_has_header_and_one_line_per_skill() -> None:
    task = _task()
    snapshot = {**_snapshot("eog.calendar"), "other": {**_snapshot("eog.email")["skill-a"], "id": "other"}}
    index = build_skill_index(snapshot, ("eog.calendar",))
    assert [entry.id for entry in index.entries] == list(SKILL_IDS)
    expected = (
        SKILLS_HEADER
        + "\n\n"
        + "\n".join(f'- agent-skill-{number}: Procedure {number} with a "quoted" word.' for number in (1, 2, 3))
    )
    assert index.block() == expected
    assert index.metadata() == [
        {
            "id": skill_id,
            "sha256": snapshot[skill_id]["sha256"],
            "name": f"agent-skill-{number}",
            "handle": f"agent-skill-{number}",
        }
        for number, skill_id in enumerate(SKILL_IDS, start=1)
    ]

    none_arm = build_agent_config(task, _tools(task), arm="none")
    skills_arm = build_agent_config(task, _tools(task), arm="skills", skill_index=index)
    assert skills_arm.system == f"{none_arm.system}\n\n{expected}"
    assert "# Procedure 1" not in skills_arm.system
    assert [tool["name"] for tool in skills_arm.tools] == ["finish", "read_skill", *task.selected_tools]
    assert skills_arm.tools[1] == READ_SKILL_TOOL
    assert READ_SKILL_TOOL["inputSchema"]["required"] == ["name"]


def test_multi_read_index_block_and_agent_config_use_the_matching_header() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    block = index.block(multi_read=True)

    assert block.startswith(SKILLS_HEADER_MULTI_READ)
    assert "read each skill whose description matches a part of the task" in block
    assert "read the skill whose description matches the task" not in block
    block_header = index.block(decline=True, multi_read=True).split("\n\n", 1)[0]
    assert block_header.endswith(SKILLS_HEADER_DECLINE)

    config = build_agent_config(
        task,
        _tools(task),
        arm="skills",
        skills_block=block,
        skill_index=index,
        skills_multi_read=True,
    )
    assert config.system.endswith(f"\n\n{block}")
    with pytest.raises(ValueError, match="The skills arm block must equal the Skill index block"):
        build_agent_config(
            task,
            _tools(task),
            arm="skills",
            skills_block=index.block(),
            skill_index=index,
            skills_multi_read=True,
        )


def test_none_arm_rejects_index_and_skills_decided_arm_rejects_tool() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    with pytest.raises(ValueError, match="no-Skills arm"):
        build_agent_config(task, _tools(task), arm="none", skill_index=index)
    with pytest.raises(ValueError, match="skills_decided arm"):
        build_agent_config(task, _tools(task), arm="skills_decided", skill_index=index)
    with pytest.raises(ValueError, match="Unknown arm"):
        build_agent_config(task, _tools(task), arm="skills_search")


def test_skills_decided_arm_keeps_selected_bodies_without_a_tool() -> None:
    task = _task()
    bodies = [_markdown(1), _markdown(2)]
    block = selected_skills_block(bodies)
    assert block == SKILLS_DECIDED_HEADER + "\n\n" + "\n\n".join(bodies)
    assert "read_skill" not in SKILLS_DECIDED_HEADER
    assert selected_skills_block([]) == ""
    none_arm = build_agent_config(task, _tools(task), arm="none")
    arm = build_agent_config(task, _tools(task), arm="skills_decided", skills_block=block)
    assert arm.system == f"{none_arm.system}\n\n{block}"
    assert [tool["name"] for tool in arm.tools] == ["finish", *task.selected_tools]


def test_skill_reader_returns_body_or_error() -> None:
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    reader = SkillReader(index)
    found = reader.call_tool("read_skill", {"name": " agent-skill-2 "})
    assert found == {"content": [{"type": "text", "text": _markdown(2)}], "isError": False}
    missing = reader.call_tool("read_skill", {"name": "agent-skill-zzzz"})
    assert missing["isError"] is True
    assert missing["content"][0]["text"] == (
        'No skill is named "agent-skill-zzzz". The Reference skills are:\n' + index.lines()
    )
    assert 'agent-skill-2: Procedure 2 with a "quoted" word.' in missing["content"][0]["text"]
    assert reader.call_tool("read_skill", "agent-skill-2")["isError"] is True
    assert reader.call_tool("other", {"name": "agent-skill-2"})["isError"] is True


def test_duplicate_skill_names_are_rejected() -> None:
    snapshot = _snapshot("eog.calendar")
    snapshot["skill-b"]["markdown"] = snapshot["skill-a"]["markdown"]
    with pytest.raises(ValueError, match="same name"):
        build_skill_index(snapshot, ("eog.calendar",))
    assert build_skill_index(snapshot, ("eog.email",)) == SkillIndex(())


class FakeRoute:
    model = "gpt-6-luna"

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[tuple[str, list[dict[str, Any]], list[str]]] = []
        self.tool_choices: list[str | None] = []

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any):
        self.calls.append((system, [dict(message) for message in messages], [tool["name"] for tool in tools]))
        self.tool_choices.append(kwargs.get("tool_choice"))
        return self.turns.pop(0)


class FakeMCP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def _turn(*calls: ToolCall, text: str = "") -> ModelTurn:
    return ModelTurn(
        text=text,
        tool_calls=tuple(calls),
        raw_usage={},
        usage=Usage(input=10, cache_read=0, cache_write=0, output=5, reasoning=0),
        latency_seconds=0.1,
    )


def test_run_agent_answers_read_skill_locally_and_records_each_call() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    first_tool = task.selected_tools[0]
    route = FakeRoute(
        [
            _turn(
                ToolCall(id="c1", name="read_skill", arguments={"name": "agent-skill-3"}),
                ToolCall(id="c2", name="read_skill", arguments={"name": "missing"}),
            ),
            _turn(ToolCall(id="c3", name=first_tool, arguments={})),
            _turn(ToolCall(id="c4", name="finish", arguments={"reason": "done"})),
        ]
    )
    mcp = FakeMCP()
    result = run_agent(
        task,
        route,
        mcp,
        _tools(task),
        effort="low",
        arm="skills",
        skill_reader=SkillReader(index),
        clock=lambda: 0.0,
    )
    assert result.finish_reason == "finished"
    assert result.read_skill_calls == (
        {"name": "agent-skill-3", "turn": 1, "found": True, "resolved_name": "agent-skill-3", "repaired_from": None},
        {"name": "missing", "turn": 1, "found": False, "resolved_name": None, "repaired_from": None},
    )
    assert result.nudged is False
    assert result.user_index_chars == 0
    assert mcp.calls == [(first_tool, {})]
    assert route.calls[0][2] == ["finish", "read_skill", *task.selected_tools]
    first_message = route.calls[0][1][0]
    assert first_message == {"role": "user", "content": task.user_prompt}
    assert route.calls[0][0].endswith(index.block())
    assert READ_SKILL_NUDGE not in [m.get("content") for m in route.calls[2][1]]
    tool_messages = [message for message in route.calls[1][1] if message.get("role") == "tool"]
    assert tool_messages[0]["content"] == _markdown(3) and tool_messages[0]["is_error"] is False
    assert tool_messages[1]["is_error"] is True
    assert tool_messages[1]["content"].startswith('No skill is named "missing".')


def test_headers_tell_the_model_when_to_read_and_skills_decided_header_names_no_tool() -> None:
    for sentence in (
        "Before your first tool call, use the read_skill tool to read the skill whose description matches the task.",
        "Adapt its steps to the task; the skill is a guide, not the task.",
        "When a tool returns an error that a skill description names, read that skill.",
        "The task is only in the user message.",
    ):
        assert sentence in SKILLS_HEADER
    for sentence in (
        "Read it before your first tool call. Adapt its steps to the task; the skill is a guide, not the task.",
        "The task is only in the user message.",
    ):
        assert sentence in SKILLS_DECIDED_HEADER
    assert "read_skill" not in SKILLS_DECIDED_HEADER
    assert "use the read_skill tool" in SKILLS_HEADER


def test_run_agent_passes_multi_read_to_the_skills_header() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    route = FakeRoute([_turn(ToolCall(id="c1", name="finish", arguments={"reason": "done"}))])
    result = run_agent(
        task,
        route,
        FakeMCP(),
        _tools(task),
        effort="low",
        arm="skills",
        skill_reader=SkillReader(index),
        clock=lambda: 0.0,
        skills_multi_read=True,
    )

    assert result.finish_reason == "finished"
    assert "read each skill whose description matches a part of the task" in route.calls[0][0]
    assert "read the skill whose description matches the task" not in route.calls[0][0]


def test_lookup_trims_folds_case_and_repairs_a_name_within_three_edits() -> None:
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    assert index.resolve(" agent-skill-2 ") == (index.entries[1], None)
    assert index.resolve("Agent-Skill-2") == (index.entries[1], "Agent-Skill-2")
    assert index.resolve("agent_skill-2") == (index.entries[1], "agent_skill-2")
    assert index.resolve("agent-skil-2x") == (index.entries[1], "agent-skil-2x")
    assert index.resolve("agent-skill-9") == (index.entries[0], "agent-skill-9")
    assert index.resolve("agent-skilled-two") == (None, None)
    assert index.resolve("") == (None, None)
    assert index.resolve(None) == (None, None)
    assert index.lookup("AGENT-SKILL-3") is index.entries[2]
    reader = SkillReader(index)
    assert reader.call_tool("read_skill", {"name": "Agent-Skill-2"})["content"][0]["text"] == _markdown(2)


def _run_skills_episode(turns: list[ModelTurn], *, with_index: bool = True):
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar" if with_index else "eog.email"), ("eog.calendar",))
    route = FakeRoute(turns)
    result = run_agent(
        task,
        route,
        FakeMCP(),
        _tools(task),
        effort="low",
        arm="skills",
        skill_reader=SkillReader(index),
        clock=lambda: 0.0,
    )
    return task, index, route, result


def test_one_nudge_after_a_first_turn_without_read_skill() -> None:
    first_tool = _task().selected_tools[0]
    _, _, route, result = _run_skills_episode(
        [
            _turn(ToolCall(id="c1", name=first_tool, arguments={})),
            _turn(ToolCall(id="c2", name=first_tool, arguments={})),
            _turn(ToolCall(id="c3", name="read_skill", arguments={"name": "Agent-Skill-1"})),
            _turn(ToolCall(id="c4", name="finish", arguments={"reason": "done"})),
        ]
    )
    assert result.nudged is True
    second_call_history = route.calls[1][1]
    assert second_call_history[-1] == {"role": "user", "content": READ_SKILL_NUDGE}
    assert second_call_history[-2]["role"] == "tool"
    nudges = [m for call in route.calls for m in call[1] if m.get("content") == READ_SKILL_NUDGE]
    assert len(nudges) == len(route.calls) - 1
    assert sum(1 for m in route.calls[-1][1] if m.get("content") == READ_SKILL_NUDGE) == 1
    assert [e["type"] for e in result.events if e["type"] == "read_skill_nudge"] == ["read_skill_nudge"]
    from eog.skills import TRACE_EVENT_TYPES

    assert {e["type"] for e in result.events} <= set(TRACE_EVENT_TYPES)
    assert TRACE_EVENT_TYPES["read_skill_nudge"] == "checkpoint"
    assert result.read_skill_calls == (
        {
            "name": "Agent-Skill-1",
            "turn": 3,
            "found": True,
            "resolved_name": "agent-skill-1",
            "repaired_from": "Agent-Skill-1",
        },
    )


def test_no_nudge_when_the_first_turn_reads_a_skill_or_makes_no_tool_call() -> None:
    first_tool = _task().selected_tools[0]
    _, _, route, result = _run_skills_episode(
        [
            _turn(ToolCall(id="c1", name="read_skill", arguments={"name": "agent-skill-1"})),
            _turn(ToolCall(id="c2", name=first_tool, arguments={})),
            _turn(ToolCall(id="c3", name="finish", arguments={"reason": "done"})),
        ]
    )
    assert result.nudged is False
    assert not any(m.get("content") == READ_SKILL_NUDGE for call in route.calls for m in call[1])
    _, _, route, result = _run_skills_episode(
        [
            _turn(text="I think first."),
            _turn(ToolCall(id="c2", name=first_tool, arguments={})),
            _turn(ToolCall(id="c3", name="finish", arguments={"reason": "done"})),
        ]
    )
    assert result.nudged is False
    assert not any(m.get("content") == READ_SKILL_NUDGE for call in route.calls for m in call[1])
    _, _, route, result = _run_skills_episode(
        [
            _turn(ToolCall(id="c1", name=first_tool, arguments={})),
            _turn(ToolCall(id="c3", name="finish", arguments={"reason": "done"})),
        ],
        with_index=False,
    )
    assert result.nudged is False and result.user_index_chars == 0
    assert route.calls[0][1][0]["content"] == _task().user_prompt


def test_none_arm_ignores_a_skill_reader() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    route = FakeRoute([_turn(ToolCall(id="c1", name="finish", arguments={"reason": "done"}))])
    result = run_agent(
        task, route, FakeMCP(), _tools(task), effort="low", arm="none", skill_reader=None, clock=lambda: 0.0
    )
    assert result.read_skill_calls == ()
    assert route.calls[0][2] == ["finish", *task.selected_tools]
    assert SKILLS_HEADER not in route.calls[0][0]
    with pytest.raises(ValueError, match="no-Skills arm"):
        run_agent(
            task, FakeRoute([]), FakeMCP(), _tools(task), effort="low", arm="none", skill_reader=SkillReader(index)
        )


def test_traced_wrapper_can_wrap_the_reader() -> None:
    from eog.tracing import TracedMCP

    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    recorded: list[dict[str, Any]] = []
    row_trace = SimpleNamespace(
        start_tool=lambda name, arguments: (len(recorded) + 1, None),
        record_tool=lambda **kwargs: recorded.append(kwargs),
    )
    wrapper = TracedMCP(SkillReader(index), row_trace)
    assert wrapper.index is index
    route = FakeRoute(
        [
            _turn(ToolCall(id="c1", name="read_skill", arguments={"name": "agent-skill-1"})),
            _turn(ToolCall(id="c2", name="finish", arguments={"reason": "done"})),
        ]
    )
    result = run_agent(
        task, route, FakeMCP(), _tools(task), effort="low", arm="skills", skill_reader=wrapper, clock=lambda: 0.0
    )
    assert result.read_skill_calls == (
        {"name": "agent-skill-1", "turn": 1, "found": True, "resolved_name": "agent-skill-1", "repaired_from": None},
    )
    assert [item["name"] for item in recorded] == ["read_skill"]
    assert recorded[0]["result"]["isError"] is False and recorded[0]["error"] is None


def test_force_first_read_sets_tool_choice_on_the_first_call_only_and_skips_the_nudge() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    first_tool = task.selected_tools[0]
    route = FakeRoute(
        [
            _turn(ToolCall(id="c1", name="read_skill", arguments={"name": "agent-skill-1"})),
            _turn(ToolCall(id="c2", name=first_tool, arguments={})),
            _turn(ToolCall(id="c3", name="finish", arguments={"reason": "done"})),
        ]
    )
    result = run_agent(
        task,
        route,
        FakeMCP(),
        _tools(task),
        effort="low",
        arm="skills",
        skill_reader=SkillReader(index),
        clock=lambda: 0.0,
        force_first_read=True,
    )
    assert route.tool_choices == ["read_skill", None, None]
    assert result.forced_first_read is True
    assert result.nudged is False
    assert result.read_skill_calls[0]["turn"] == 1
    none_route = FakeRoute([_turn(ToolCall(id="c1", name="finish", arguments={"reason": "done"}))])
    none_result = run_agent(task, none_route, FakeMCP(), _tools(task), effort="low", arm="none", force_first_read=True)
    assert none_route.tool_choices == [None]
    assert none_result.forced_first_read is False


def test_force_first_read_skips_a_route_without_forced_tool_choice_support() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    route = FakeRoute(
        [
            _turn(ToolCall(id="c1", name="read_skill", arguments={"name": "agent-skill-1"})),
            _turn(ToolCall(id="c3", name="finish", arguments={"reason": "done"})),
        ]
    )
    route.supports_forced_tool_choice = False
    result = run_agent(
        task,
        route,
        FakeMCP(),
        _tools(task),
        effort="low",
        arm="skills",
        skill_reader=SkillReader(index),
        clock=lambda: 0.0,
        force_first_read=True,
    )
    assert route.tool_choices == [None, None]
    assert result.forced_first_read is False


def test_skill_name_enum_lists_the_index_names_and_none_on_the_read_skill_tool() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    plain = build_agent_config(task, _tools(task), arm="skills", skills_block=index.block(), skill_index=index)
    plain_tool = next(tool for tool in plain.tools if tool["name"] == "read_skill")
    assert "enum" not in plain_tool["inputSchema"]["properties"]["name"]
    config = build_agent_config(
        task,
        _tools(task),
        arm="skills",
        skills_block=index.block(decline=True),
        skill_index=index,
        skill_name_enum=True,
    )
    tool = next(tool for tool in config.tools if tool["name"] == "read_skill")
    names = tool["inputSchema"]["properties"]["name"]["enum"]
    assert names == [*(entry.name for entry in index.entries), "none"]
    assert '"none"' in tool["inputSchema"]["properties"]["name"]["description"]
    assert "enum" not in READ_SKILL_TOOL["inputSchema"]["properties"]["name"]


def test_read_skill_none_choice_is_not_an_error_and_is_not_a_found_read() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    route = FakeRoute(
        [
            _turn(ToolCall(id="c1", name="read_skill", arguments={"name": "none"})),
            _turn(ToolCall(id="c2", name="finish", arguments={"reason": "done"})),
        ]
    )
    result = run_agent(
        task,
        route,
        FakeMCP(),
        _tools(task),
        effort="low",
        arm="skills",
        skill_reader=SkillReader(index),
        clock=lambda: 0.0,
        force_first_read=True,
        skill_name_enum=True,
    )
    assert list(result.read_skill_calls) == [
        {"name": "none", "turn": 1, "found": False, "resolved_name": None, "repaired_from": None}
    ]
    assert result.nudged is False
    tool_messages = [item for item in result.history if item.get("role") == "tool"]
    assert tool_messages and tool_messages[0]["is_error"] is False
    assert tool_messages[0]["content"] == READ_SKILL_NONE_TEXT


def test_skill_name_enum_names_the_none_option_in_the_skills_header() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    plain = build_agent_config(task, _tools(task), arm="skills", skills_block=index.block(), skill_index=index)
    assert SKILLS_HEADER_DECLINE not in plain.system
    config = build_agent_config(
        task,
        _tools(task),
        arm="skills",
        skills_block=index.block(decline=True),
        skill_index=index,
        skill_name_enum=True,
    )
    assert SKILLS_HEADER_DECLINE in config.system
    assert config.system.count(index.lines()) == 1


def test_short_ids_name_each_skill_by_the_first_8_hex_characters_of_its_uuid() -> None:
    import uuid

    from eog.agent import SHORT_ID_LENGTH, SkillReader, read_skill_tool

    snapshot = {
        str(uuid.uuid5(uuid.NAMESPACE_URL, skill_id)): {**skill, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, skill_id))}
        for skill_id, skill in _snapshot("eog.calendar").items()
    }
    index = build_skill_index(snapshot, ("eog.calendar",), short_ids=True)
    first = index.entries[0]
    handle = index.handle(first)
    assert handle == uuid.UUID(first.id).hex[:SHORT_ID_LENGTH]
    assert len(handle) == SHORT_ID_LENGTH
    assert index.lines().startswith(f"- {handle}: ")
    assert first.name not in index.lines()
    assert index.lookup(handle) is first
    assert index.lookup(f" {handle.upper()} ") is first
    assert index.lookup(first.name) is first
    assert index.lookup("00000000") is None
    assert index.metadata()[0]["handle"] == handle
    tool = read_skill_tool(index)
    assert "enum" not in tool["inputSchema"]["properties"]["name"]
    assert "short id" in tool["inputSchema"]["properties"]["name"]["description"]
    tool = read_skill_tool(index, enum=True)
    assert tool["inputSchema"]["properties"]["name"]["enum"] == [*(index.handle(e) for e in index.entries), "none"]
    reader = SkillReader(index)
    assert reader.call_tool("read_skill", {"name": handle})["isError"] is False
    plain = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    assert plain.handle(plain.entries[0]) == plain.entries[0].name
    with pytest.raises(ValueError, match="no UUID id"):
        build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",), short_ids=True)


def test_system_preamble_is_prepended_before_the_skill_index() -> None:
    task = _task()
    index = build_skill_index(_snapshot("eog.calendar"), ("eog.calendar",))
    preamble = "You are a business operations agent. You are authorized to execute tasks on behalf of employees."
    old = build_agent_config(task, _tools(task), arm="none").system
    config = build_agent_config(task, _tools(task), arm="none", system_preamble=preamble)
    skills_old = build_agent_config(task, _tools(task), arm="skills", skill_index=index).system
    skills = build_agent_config(task, _tools(task), arm="skills", skill_index=index, system_preamble=preamble)

    assert config.system == f"{preamble}\n\n{old}"
    assert skills.system == f"{preamble}\n\n{skills_old}"
    assert skills.system.endswith(index.block())


def test_no_system_preamble_keeps_the_system_prompt_unchanged() -> None:
    task = _task()
    expected = (
        f"{BASE_SYSTEM_PROMPT_TEMPLATE.format(max_turns=MAX_TURNS)}"
        f"{NO_USER_INPUT_GUIDANCE}{TASK_INSTRUCTIONS_HEADER}{task.system_prompt}"
    )

    assert build_agent_config(task, _tools(task), arm="none").system == expected
    assert build_agent_config(task, _tools(task), arm="none", system_preamble=None).system == expected
