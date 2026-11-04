import hashlib
import json
from collections.abc import Sequence
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from openai import BadRequestError
from tau2.data_model.message import MultiToolMessage, ToolMessage, UserMessage
from tau2.environment.tool import Tool
from zep_cloud.core.pagination import SyncPager

from agent import (
    CURRENT_EPISODE,
    AgentContextLimitError,
    AgentResponseFailedError,
    ZepResponsesAgent,
    _responses_tool,
    create_zep_responses_agent,
    hash_prompt_cache_key,
)
from redaction import Redactor
from skill_index import SnapshotSkillToolkit
from tracing import response_cache_creation_tokens
from zep_capture import SkillToolkit


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.tool_calls: list[tuple[str, object, str | None]] = []
        self.tool_results: list[tuple[str, object, str | None]] = []

    def record_message(self, role: str, text: str) -> None:
        self.events.append((role, text))

    def record_tool_call(self, name: str, arguments: object, *, call_id: str | None = None) -> None:
        self.events.append((name, str(arguments)))
        self.tool_calls.append((name, arguments, call_id))

    def record_tool_result(self, name: str, content: object, *, call_id: str | None = None) -> None:
        self.events.append((name, str(content)))
        self.tool_results.append((name, content, call_id))


class Responses:
    def __init__(self, outputs: Sequence[Sequence[object]]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=self.outputs[len(self.calls) - 1],
            usage=SimpleNamespace(
                input_tokens=10,
                input_tokens_details=SimpleNamespace(cached_tokens=5),
                output_tokens=4,
                output_tokens_details=SimpleNamespace(reasoning_tokens=3),
            ),
        )


def _agent(
    outputs: Sequence[Sequence[object]], prompt_cache_key: str | None = None
) -> tuple[ZepResponsesAgent, Responses]:
    responses = Responses(outputs)
    return (
        ZepResponsesAgent(
            [],
            "Follow the policy.",
            openai_client=SimpleNamespace(responses=responses),
            agent_model="test-model",
            reasoning_effort="high",
            recorder=Recorder(),
            skills=[],
            prompt_cache_key=prompt_cache_key,
        ),
        responses,
    )


def test_responses_tool_uses_flat_function_schema() -> None:
    def lookup(query: str) -> str:
        """Look up an item.

        Args:
            query: The item to look up.
        Returns:
            The lookup result.
        """
        return query

    schema = _responses_tool(Tool(lookup))
    assert schema["type"] == "function"
    assert schema["name"] == "lookup"
    assert schema["description"]
    assert "parameters" in schema
    assert "function" not in schema
    assert "strict" not in schema


def test_text_response() -> None:
    agent, _ = _agent(
        [
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Done."}],
                )
            ]
        ]
    )
    message, state = agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    assert message.content == "Done."
    assert message.tool_calls is None
    assert state.reasoning_tokens == 3


def _failed_agent(message: str) -> ZepResponsesAgent:
    response = SimpleNamespace(
        status="failed",
        output=[],
        usage=None,
        error=SimpleNamespace(code="invalid_prompt", message=message),
    )
    return ZepResponsesAgent(
        [],
        "Follow the policy.",
        openai_client=SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response)),
        agent_model="test-model",
        reasoning_effort="high",
        recorder=Recorder(),
        skills=[],
    )


def test_failed_response_over_the_context_window_raises_context_limit_error() -> None:
    agent = _failed_agent("This model's maximum context length is 262144 tokens.")
    with pytest.raises(AgentContextLimitError, match="maximum context length"):
        agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())


def test_context_length_bad_request_raises_context_limit_error_without_retry() -> None:
    calls: list[dict] = []

    def create(**kwargs: object) -> object:
        calls.append(kwargs)
        raise BadRequestError(
            "This endpoint's maximum context length is 262144 tokens.",
            response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
            body=None,
        )

    agent = ZepResponsesAgent(
        [],
        "Follow the policy.",
        openai_client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        agent_model="test-model",
        reasoning_effort="high",
        recorder=Recorder(),
        skills=[],
    )
    with pytest.raises(AgentContextLimitError, match="maximum context length"):
        agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    assert len(calls) == 1


def test_other_failed_response_raises_response_failed_error() -> None:
    agent = _failed_agent("The provider is not available.")
    with pytest.raises(AgentResponseFailedError) as raised:
        agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    assert not isinstance(raised.value, AgentContextLimitError)


def test_usage_totals_accumulate_every_response() -> None:
    output = [
        SimpleNamespace(
            type="message", role="assistant", content=[{"type": "output_text", "text": "Done."}]
        )
    ]
    agent, _ = _agent([output, output])
    agent.usage_totals = {}
    state = agent.get_init_state()
    _, state = agent.generate_next_message(UserMessage.text("Hello"), state)
    agent.generate_next_message(UserMessage.text("Again"), state)
    assert agent.usage_totals.pop("model_ms") >= 0
    assert agent.usage_totals == {
        "prompt_tokens": 20,
        "cached_tokens": 10,
        "cache_creation_tokens": 0,
        "completion_tokens": 8,
        "reasoning_tokens": 6,
    }


@pytest.mark.parametrize(
    "alias", ["cache_creation_tokens", "cache_write_tokens", "cache_creation_input_tokens"]
)
@pytest.mark.parametrize("location", ["input_tokens_details", "prompt_tokens_details", None])
def test_cache_creation_aliases_accumulate(alias, location):
    output = [
        SimpleNamespace(
            type="message", role="assistant", content=[{"type": "output_text", "text": "Done."}]
        )
    ]
    agent, responses = _agent([output, output])
    original = responses.create

    def create(**kwargs):
        response = original(**kwargs)
        target = response.usage
        if location:
            target = SimpleNamespace()
            setattr(response.usage, location, target)
        setattr(target, alias, 123)
        return response

    responses.create = create
    agent.usage_totals = {}
    state = agent.get_init_state()
    for _ in range(2):
        message, state = agent.generate_next_message(UserMessage.text("Hello"), state)
        assert message.usage["cache_creation_tokens"] == 123
    assert state.cache_creation_tokens == 246
    assert agent.usage_totals["cache_creation_tokens"] == 246


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens_details": None}])
def test_missing_cache_creation_usage_is_zero(usage):
    assert response_cache_creation_tokens(usage) == 0


def test_cache_creation_aliases_are_not_added_together():
    assert (
        response_cache_creation_tokens(
            {
                "input_tokens_details": {"cache_creation_tokens": 123, "cache_write_tokens": 123},
                "cache_creation_input_tokens": 123,
            }
        )
        == 123
    )
    assert (
        response_cache_creation_tokens(
            {"input_tokens_details": {"cache_creation_tokens": 0, "cache_write_tokens": 123}}
        )
        == 123
    )
    with pytest.raises(ValueError):
        response_cache_creation_tokens({"cache_write_tokens": "invalid"})


def test_max_output_tokens_is_sent_only_when_set() -> None:
    output = [
        SimpleNamespace(
            type="message", role="assistant", content=[{"type": "output_text", "text": "Done."}]
        )
    ]
    agent, responses = _agent([output])
    agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    assert "max_output_tokens" not in responses.calls[0]
    agent, responses = _agent([output])
    agent.max_output_tokens = 32768
    agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    assert responses.calls[0]["max_output_tokens"] == 32768


def test_request_uses_episode_cache_key_and_accumulates_cached_tokens() -> None:
    composed_key = f"full-tau2-teacher-2:telecom:{'long-task-id-' * 8}:1"
    sent_key = hash_prompt_cache_key(composed_key)
    agent, responses = _agent(
        [
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "First"}],
                )
            ],
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Second"}],
                )
            ],
        ],
        prompt_cache_key=sent_key,
    )
    state = agent.get_init_state()
    _, state = agent.generate_next_message(UserMessage.text("First"), state)
    _, state = agent.generate_next_message(UserMessage.text("Second"), state)
    assert responses.calls[0]["prompt_cache_key"] == sent_key
    assert len(responses.calls[0]["prompt_cache_key"]) == 64
    assert sent_key == hashlib.sha256(composed_key.encode()).hexdigest()
    assert state.cached_tokens == 10


def test_function_call_response() -> None:
    agent, _ = _agent(
        [
            [
                SimpleNamespace(
                    type="function_call", name="lookup", arguments='{"id": 1}', call_id="call-1"
                )
            ]
        ]
    )
    message, _ = agent.generate_next_message(UserMessage.text("Find it."), agent.get_init_state())
    assert message.content is None
    assert message.tool_calls is not None
    assert message.tool_calls[0].name == "lookup"
    assert message.tool_calls[0].arguments == {"id": 1}


def test_function_call_with_text_drops_text() -> None:
    agent, _ = _agent(
        [
            [
                SimpleNamespace(
                    type="function_call", name="lookup", arguments="{}", call_id="call-1"
                ),
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Hidden"}],
                ),
            ]
        ]
    )
    message, state = agent.generate_next_message(
        UserMessage.text("Find it."), agent.get_init_state()
    )
    assert message.tool_calls
    assert message.content is None
    assert state.dropped_text_with_tool_call == 1


def test_second_request_contains_function_call_output() -> None:
    agent, responses = _agent(
        [
            [
                SimpleNamespace(type="reasoning", id="r1", summary=[]),
                SimpleNamespace(
                    type="function_call", name="lookup", arguments="{}", call_id="call-1"
                ),
            ],
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Done"}],
                )
            ],
        ]
    )
    state = agent.get_init_state()
    first, state = agent.generate_next_message(UserMessage.text("Find it."), state)
    assert first.tool_calls is not None
    agent.generate_next_message(
        ToolMessage(
            id=first.tool_calls[0].id,
            role="tool",
            content="ok",
            requestor="assistant",
        ),
        state,
    )
    second_input = responses.calls[1]["input"]
    assert ("lookup", "ok") in agent.recorder.events
    assert any(item.get("type") == "function_call_output" for item in second_input)
    assert any(item.get("type") == "function_call" for item in second_input)
    assert any(item.get("type") == "reasoning" for item in second_input)


def test_agent_records_tool_call_ids() -> None:
    def lookup(item_id: int) -> str:
        """Look up an item."""
        return str(item_id)

    recorder = Recorder()
    responses = Responses(
        [
            [
                SimpleNamespace(
                    type="function_call",
                    name="lookup",
                    arguments='{"item_id": 1}',
                    call_id="call-1",
                )
            ],
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Done."}],
                )
            ],
        ]
    )
    agent = ZepResponsesAgent(
        [Tool(lookup)],
        "Follow the policy.",
        openai_client=SimpleNamespace(responses=responses),
        agent_model="test-model",
        reasoning_effort="high",
        recorder=recorder,
        skills=[],
    )
    message, state = agent.generate_next_message(
        UserMessage.text("Look up item 1."), agent.get_init_state()
    )
    assert message.tool_calls is not None

    result, _ = agent.generate_next_message(
        ToolMessage(id="call-1", role="tool", content="Item found.", requestor="assistant"),
        state,
    )

    assert result.content == "Done."
    assert recorder.tool_calls == [("lookup", {"item_id": 1}, "call-1")]
    assert recorder.tool_results == [("lookup", "Item found.", "call-1")]


def test_multi_tool_message_appends_each_output() -> None:
    agent, responses = _agent(
        [
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Done"}],
                )
            ]
        ]
    )
    state = agent.get_init_state()
    state.pending_calls.update({"a": "first_tool", "b": "second_tool"})
    agent.generate_next_message(
        MultiToolMessage(
            role="tool",
            tool_messages=[
                ToolMessage(id="a", role="tool", content="A", requestor="assistant"),
                ToolMessage(id="b", role="tool", content="B", requestor="assistant"),
            ],
        ),
        state,
    )
    assert (
        len(
            [item for item in responses.calls[0]["input"] if item["type"] == "function_call_output"]
        )
        == 2
    )
    assert ("first_tool", "A") in agent.recorder.events
    assert ("second_tool", "B") in agent.recorder.events


def test_unknown_tool_output_call_id_raises() -> None:
    agent, _ = _agent([[]])
    with pytest.raises(RuntimeError, match="Unknown tool call id"):
        agent.generate_next_message(
            ToolMessage(id="unknown", role="tool", content="result", requestor="assistant"),
            agent.get_init_state(),
        )


def test_learned_skills_use_the_standard_appendix() -> None:
    agent, _ = _agent([[]])
    agent.skills = [{"markdown": "### Procedure\nDo the task."}]
    assert (
        "## Learned skills\n"
        "The following procedures were compiled from earlier successful runs on this task family.\n"
        "Use a procedure when its preconditions hold. Follow the policy above when they conflict.\n\n"
        "### Procedure\nDo the task."
    ) in agent.instructions


SKILL_MARKDOWN = (
    "# Cancel a reservation\n\n## Invocation\n\nCancel a reservation.\n\n"
    "Use this Skill when:\n\n- The user asks to cancel a reservation. <!-- zep-evidence: e1 -->\n"
    "\n## Procedure\n\n1. Check the cancellation policy first.\n"
)


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="message", role="assistant", content=[{"type": "output_text", "text": text}]
    )


def _load(call_id: str, skill_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name="load_skill",
        call_id=call_id,
        arguments=f'{{"skill_id": "{skill_id}"}}',
    )


def _skill_agent(outputs: list[list[object]]) -> tuple[ZepResponsesAgent, Responses, MagicMock]:
    agent, responses = _agent(outputs)
    client = MagicMock()
    client.agent.skill.search.return_value = SyncPager(
        has_next=False,
        items=[
            {
                "uuid": "skill-1",
                "name": "Cancel a reservation",
                "description": "Cancel a reservation after a policy check.",
                "markdown": SKILL_MARKDOWN,
            }
        ],
        get_next=None,
        response={"search_id": "search-1"},
    )
    agent.skill_toolkit = SkillToolkit(client, "agent-uuid", "tool.airline")
    return agent, responses, client


def test_skill_catalog_shows_metadata_until_the_agent_loads_a_skill() -> None:
    agent, responses, client = _skill_agent(
        [[_load("load-1", "skill-1")], [_message("I will check the policy.")], [_message("OK")]]
    )
    redactor = Redactor()
    redactor._add("<RESERVATION_1>", "AB1234")
    agent.redactor = redactor
    state = agent.get_init_state()

    message, _ = agent.generate_next_message(UserMessage.text("Cancel reservation AB1234."), state)

    assert message.content == "I will check the policy."
    first = responses.calls[0]
    assert "use when: The user asks to cancel a reservation." in first["instructions"]
    assert "zep-evidence" not in first["instructions"]
    assert "Check the cancellation policy first." not in first["instructions"]
    assert "load_skill" in [tool["name"] for tool in first["tools"]]
    outputs = [item for item in responses.calls[1]["input"] if item.get("call_id") == "load-1"]
    assert outputs[-1]["output"] == SKILL_MARKDOWN
    assert agent.skill_toolkit is not None
    assert [skill["uuid"] for skill in agent.skill_toolkit.loaded] == ["skill-1"]
    assert all(name != "load_skill" for name, _ in agent.recorder.events)
    assert state.prompt_tokens == 20
    assert "AB1234" not in client.agent.skill.search.call_args.kwargs["query"]

    agent.generate_next_message(UserMessage.text("Please continue."), state)
    assert client.agent.skill.search.call_count == 1


def test_agent_can_skip_every_skill() -> None:
    agent, responses, _ = _skill_agent([[_message("I can help.")]])

    agent.generate_next_message(UserMessage.text("Book a flight."), agent.get_init_state())

    assert agent.skill_toolkit is not None
    assert agent.skill_toolkit.loaded == []
    assert len(responses.calls) == 1


@pytest.mark.parametrize("native", [False, True])
def test_episode_audit_captures_reasoning_without_changing_visible_messages(native):
    reasoning = (
        {
            "type": "anthropic_content",
            "content": [{"type": "thinking", "thinking": "Check the terms."}],
        }
        if native
        else {"type": "reasoning", "summary": [{"text": "Check the terms."}]}
    )
    agent, responses = _agent([[reasoning, _message("Done.")]])
    responses.native_thinking = native
    message, _ = agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    request = responses.calls[0]
    assert request["reasoning"] == (
        {"effort": "high"} if native else {"effort": "high", "summary": "auto"}
    )
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["store"] is False
    assert message.content == "Done."
    assert message.raw_data is None
    audit = agent.episode_audit.export()
    captured = audit["assistant_turns"][0]["requests"][0]
    assert captured["summary_mode"] == ("provider_native" if native else "requested")
    assert captured["reasoning_status"] == "returned"
    assert captured["reasoning"][0]["text"] == "Check the terms."


def test_summary_option_rejection_is_disabled_for_the_shared_provider_route(monkeypatch):
    monkeypatch.setattr("agent.SUMMARY_UNAVAILABLE_ROUTES", set())
    calls = []

    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        if "summary" in kwargs["reasoning"]:
            raise BadRequestError(
                "Unsupported parameter: reasoning.summary",
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.com")),
                body={"error": {"param": "reasoning.summary"}},
            )
        return SimpleNamespace(output=[_message("Done.")], usage=None)

    agent, _ = _agent([])
    agent.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    state = agent.get_init_state()
    agent.generate_next_message(UserMessage.text("Hello"), state)
    assert len(calls) == 2
    rejected = deepcopy(calls[0])
    rejected["reasoning"].pop("summary")
    assert rejected == calls[1]
    other, _ = _agent([])
    other.openai_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    other.generate_next_message(UserMessage.text("Hello"), other.get_init_state())
    assert len(calls) == 3
    assert calls[-1]["reasoning"] == {"effort": "high"}
    requests = agent.episode_audit.export()["assistant_turns"][0]["requests"]
    assert [item["reasoning_status"] for item in requests] == ["unavailable", "unavailable"]
    assert requests[0]["error"] == "summary_option_rejected"
    assert (
        other.episode_audit.export()["assistant_turns"][0]["requests"][0]["summary_mode"]
        == "unsupported"
    )


@pytest.mark.parametrize(
    "name,found,repaired",
    [
        ("procedure", True, False),
        ("procedur", True, True),
        ("unrelated-name", False, False),
        ("none", True, False),
    ],
)
def test_internal_read_skill_audit_preserves_positions_and_evaluator_omission(
    name, found, repaired
):
    read = {
        "type": "function_call",
        "name": "read_skill",
        "call_id": "read",
        "arguments": json.dumps({"name": name}),
    }
    shell = {
        "type": "function_call",
        "name": "shell",
        "call_id": "shell",
        "arguments": '{"command":"ls"}',
    }
    agent, _ = _agent([[read], [shell], [_message("Done.")]])
    agent.skill_toolkit = SnapshotSkillToolkit(
        [
            {
                "uuid": "skill",
                "name": "procedure",
                "description": "Procedure.",
                "markdown": "Do the task.",
            },
        ]
    )
    message, state = agent.generate_next_message(UserMessage.text("Help"), agent.get_init_state())
    assert [call.name for call in message.tool_calls] == ["shell"]
    agent.generate_next_message(
        ToolMessage(role="tool", id="shell", name="shell", content="files"), state
    )
    assert all(call[0] != "read_skill" for call in agent.recorder.tool_calls)
    audit = agent.episode_audit.export()
    read = audit["skill_read_positions"][0]
    assert read["name"] == name
    assert read["found"] is found
    assert read.get("repaired", False) is repaired
    assert read["assistant_turn_index"] == 1
    assert read["tool_call_index"] == 1
    assert read["next_tool_call"]["tool"] == "shell"
    assert read["tool_calls_after"] == [2]
    assert len(audit["assistant_turns"][0]["requests"]) == 2
    assert len(audit["assistant_turns"]) == 2


def test_failed_response_keeps_partial_reasoning_audit():
    agent = _failed_agent("Provider failed.")
    with pytest.raises(AgentResponseFailedError):
        agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())
    request = agent.episode_audit.export()["assistant_turns"][0]["requests"][0]
    assert request["reasoning"] == []
    assert request["reasoning_status"] == "not_returned"


def test_unknown_skill_id_loads_nothing() -> None:
    agent, responses, _ = _skill_agent([[_load("load-1", "other")], [_message("I can help.")]])

    agent.generate_next_message(UserMessage.text("Cancel it."), agent.get_init_state())

    assert agent.skill_toolkit is not None
    assert agent.skill_toolkit.loaded == []
    assert agent.skill_toolkit.invalid_loads == 1
    outputs = [item for item in responses.calls[1]["input"] if item.get("call_id") == "load-1"]
    assert "Unknown skill_id" in outputs[-1]["output"]


def test_skill_load_turns_are_limited() -> None:
    outputs: list[list[object]] = [[_load(f"load-{index}", "skill-1")] for index in range(3)]
    agent, responses, _ = _skill_agent([*outputs, [_message("Done.")]])

    agent.generate_next_message(UserMessage.text("Cancel it."), agent.get_init_state())

    assert len(responses.calls) == 4
    assert "load_skill" not in [tool["name"] for tool in responses.calls[-1]["tools"]]
    assert agent.skill_toolkit is not None
    assert len(agent.skill_toolkit.loaded) == 1


def test_missing_reasoning_effort_raises() -> None:
    agent, _ = _agent(
        [
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[{"type": "output_text", "text": "Done"}],
                )
            ]
        ]
    )
    agent.reasoning_effort = ""
    with pytest.raises(ValueError):
        agent.generate_next_message(UserMessage.text("Hello"), agent.get_init_state())


def test_factory_reads_context_and_requires_context() -> None:
    token = CURRENT_EPISODE.set(
        {
            "openai_client": SimpleNamespace(responses=Responses([[]])),
            "agent_model": "test",
            "reasoning_effort": "low",
            "recorder": Recorder(),
            "skills": [],
        }
    )
    assert isinstance(create_zep_responses_agent([], "policy"), ZepResponsesAgent)
    CURRENT_EPISODE.reset(token)
    token = CURRENT_EPISODE.set(None)
    with pytest.raises(RuntimeError):
        create_zep_responses_agent([], "policy")
    CURRENT_EPISODE.reset(token)


def test_briefing_is_added_after_the_first_user_message() -> None:
    done = SimpleNamespace(
        type="message", role="assistant", content=[{"type": "output_text", "text": "Done."}]
    )
    agent, responses = _agent([[done]])
    agent.memory_provider = SimpleNamespace(brief=lambda query: f"Plan for: {query}")
    state = agent.get_init_state()
    agent.generate_next_message(UserMessage(role="user", content="cancel my flight"), state)
    instructions = responses.calls[0]["instructions"]
    assert "## Task briefing" in instructions
    assert "Plan for: cancel my flight" in instructions
    assert instructions.index("Follow the policy.") < instructions.index("## Task briefing")
