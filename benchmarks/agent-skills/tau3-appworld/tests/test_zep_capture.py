from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from zep_cloud import Zep
from zep_cloud.core.pagination import SyncPager
from zep_cloud.errors.conflict_error import ConflictError
from zep_cloud.types.agent import Agent
from zep_cloud.types.api_error import ApiError as ApiErrorBody
from zep_cloud.types.error_body import ErrorBody

import zep_capture
from redaction import Redactor
from zep_capture import SkillToolkit, TrajectoryRecorder, _idempotency_key, model_dict


def _recorder_for_close(client: object) -> TrajectoryRecorder:
    return TrajectoryRecorder(
        cast(Zep, client),
        "agent-uuid",
        "tool.airline.task000",
        "objective",
        "environment",
        {},
        redactor=Redactor(),
        stage="teacher",
        run_id="close-retry-test",
        domain="airline",
        task_index=0,
        trial=1,
        verifier_id="appworld-evaluation",
        discoverable_tools=frozenset(),
    )


def _verifier_registration_conflict() -> ConflictError:
    return ConflictError(
        ApiErrorBody(
            error=ErrorBody(
                code="conflict",
                message="conflict: a verifier with that verifier_id already exists for the Agent",
            )
        )
    )


def _other_close_conflict() -> ConflictError:
    return ConflictError(
        ApiErrorBody(error=ErrorBody(code="conflict", message="revision mismatch"))
    )


def test_model_dict_uses_sdk_field_aliases() -> None:
    agent = Agent.model_construct(uuid_="agent-uuid")

    assert model_dict(agent) == {"uuid": "agent-uuid"}


def test_trajectory_recorder_retries_verifier_registration_conflict(
    caplog: pytest.LogCaptureFixture,
) -> None:
    close_result = SimpleNamespace(status="closed")
    close = Mock(side_effect=[_verifier_registration_conflict(), close_result])
    client = SimpleNamespace(
        agent=SimpleNamespace(
            trace=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(uuid="trajectory-uuid")),
                close=close,
            )
        )
    )
    recorder = _recorder_for_close(client)

    with caplog.at_level("WARNING", logger="zep_capture"), recorder:
        recorder.set_result(True, True)

    assert close.call_count == 2
    first, second = close.call_args_list
    assert first.args == second.args == ("agent-uuid", "trajectory-uuid")
    assert first.kwargs["outcome"] == second.kwargs["outcome"] == "succeeded"
    assert first.kwargs["verification"] == second.kwargs["verification"] == "external"
    assert first.kwargs["verifier"] == second.kwargs["verifier"]
    assert first.kwargs["verifier"].verifier_id == "appworld-evaluation"
    assert first.kwargs["idempotency_key"] == _idempotency_key("close:trajectory-uuid")
    assert second.kwargs["idempotency_key"] == _idempotency_key("close-retry:trajectory-uuid")
    assert first.kwargs["idempotency_key"] != second.kwargs["idempotency_key"]
    assert recorder.close_result is close_result
    assert [record.getMessage() for record in caplog.records if record.levelname == "WARNING"] == [
        "Retrying Trace close after a verifier registration conflict"
    ]


def test_trajectory_recorder_does_not_retry_an_unrelated_conflict() -> None:
    close = Mock(side_effect=_other_close_conflict())
    client = SimpleNamespace(
        agent=SimpleNamespace(
            trace=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(uuid="trajectory-uuid")),
                close=close,
            )
        )
    )
    recorder = _recorder_for_close(client)

    with pytest.raises(ConflictError, match="revision mismatch"), recorder:
        recorder.set_result(True, True)

    close.assert_called_once()


def test_trajectory_recorder_reraises_a_failed_retry() -> None:
    close = Mock(side_effect=[_verifier_registration_conflict(), _verifier_registration_conflict()])
    client = SimpleNamespace(
        agent=SimpleNamespace(
            trace=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(uuid="trajectory-uuid")),
                close=close,
            )
        )
    )
    recorder = _recorder_for_close(client)

    with (
        pytest.raises(ConflictError, match="verifier with that verifier_id already exists"),
        recorder,
    ):
        recorder.set_result(True, True)

    assert close.call_count == 2


def test_search_skills_does_not_fetch_later_pages() -> None:
    second_page = SyncPager(
        get_next=None,
        has_next=False,
        items=[{"uuid": "second-skill"}],
        response=SimpleNamespace(search_id="second-search"),
    )
    get_next = Mock(return_value=second_page)
    first_page = SyncPager(
        get_next=get_next,
        has_next=True,
        items=[{"uuid": "first-skill"}],
        response=SimpleNamespace(search_id="first-search"),
    )
    client = SimpleNamespace(
        agent=SimpleNamespace(skill=SimpleNamespace(search=Mock(return_value=first_page)))
    )
    toolkit = SkillToolkit(cast(Zep, client), "agent-uuid", "tool.airline")

    assert toolkit.search_skills("query", limit=5) == [{"uuid": "first-skill"}]
    get_next.assert_not_called()


@pytest.mark.parametrize(("limit", "page_size"), [(20, 20), (3, 3)])
def test_search_skills_uses_the_wrapper_limit_as_page_size(limit: int, page_size: int) -> None:
    search = Mock(
        return_value=SyncPager(
            get_next=None,
            has_next=False,
            items=[],
            response=SimpleNamespace(search_id="search"),
        )
    )
    client = SimpleNamespace(agent=SimpleNamespace(skill=SimpleNamespace(search=search)))
    toolkit = SkillToolkit(cast(Zep, client), "agent-uuid", "tool.airline")

    toolkit.search_skills("query", limit=limit)

    assert search.call_args.kwargs["page_size"] == page_size


def test_discoverable_calls_name_the_real_tool_in_the_event_context() -> None:
    from redaction import Redactor
    from zep_capture import TrajectoryRecorder, context_tool_name

    known = frozenset({"submit_x_0589", "submit_cash_back_dispute_0589"})
    assert context_tool_name("KB_search", {"query": "fees"}, known) == "KB_search"
    assert (
        context_tool_name(
            "give_discoverable_user_tool", {"discoverable_tool_name": "submit_x_0589"}, known
        )
        == "give_discoverable_user_tool:submit_x_0589"
    )
    assert (
        context_tool_name("unlock_discoverable_agent_tool", {}, known)
        == "unlock_discoverable_agent_tool"
    )
    assert (
        context_tool_name("call_discoverable_agent_tool", {"agent_tool_name": "cust_4821"}, known)
        == "call_discoverable_agent_tool"
    )

    client = Mock()
    recorder = TrajectoryRecorder(
        client,
        "agent",
        "tool.banking_knowledge",
        "objective",
        "tau2-bench-banking-knowledge",
        {},
        redactor=Redactor(),
        stage="teacher",
        run_id="run",
        domain="banking_knowledge",
        task_index=0,
        trial=0,
        discoverable_tools=known,
    )
    recorder.trajectory_uuid = "trace"
    recorder.record_tool_call(
        "call_discoverable_agent_tool",
        {"agent_tool_name": "submit_cash_back_dispute_0589", "arguments": "{}"},
        call_id="call-1",
    )
    recorder.record_tool_result("call_discoverable_agent_tool", "ok", call_id="call-1")
    tools = [
        call.kwargs["context"].tools[0].name
        for call in client.agent.trace.append_event.call_args_list
    ]
    assert tools == [
        "call_discoverable_agent_tool:submit_cash_back_dispute_0589",
        "call_discoverable_agent_tool:submit_cash_back_dispute_0589",
    ]


def test_banking_discoverable_tool_names_come_from_the_toolkit() -> None:
    from run import discoverable_tool_names

    names = discoverable_tool_names("banking_knowledge")
    assert "submit_cash_back_dispute_0589" in names
    assert "call_discoverable_agent_tool" not in names


def test_set_literal_policy_allows_tool_and_environment_names() -> None:
    literal_policy = SimpleNamespace(
        get=Mock(return_value={"revision": 3}),
        update=Mock(),
    )
    client = SimpleNamespace(agent=SimpleNamespace(literal_policy=literal_policy))
    zep_capture.set_literal_policy(
        cast(Zep, client),
        "agent-1",
        tools=["b_tool", "a_tool"],
        environments=["env"],
        idempotency_prefix="run",
    )
    literal_policy.get.assert_called_once_with("agent-1")
    args, kwargs = literal_policy.update.call_args
    assert args == ("agent-1",)
    assert kwargs["expected_revision"] == 3
    assert kwargs["idempotency_key"]
    assert kwargs["default"] == "reject"
    assert kwargs["allowlisted_classes"].model_dump(mode="json") == {
        "tools": ["command_name"],
        "environments": ["environment_name"],
    }
    assert kwargs["allowlisted_values"].model_dump(mode="json") == {
        "tools": ["a_tool", "b_tool"],
        "environments": ["env"],
    }


def test_set_literal_policy_rejects_too_many_values() -> None:
    with pytest.raises(ValueError, match="at most 256 tools"):
        zep_capture.set_literal_policy(
            cast(Zep, object()),
            "agent-1",
            tools=[f"tool_{index}" for index in range(257)],
            environments=[],
            idempotency_prefix="run",
        )


def test_set_literal_policy_skips_an_equal_policy() -> None:
    literal_policy = SimpleNamespace(
        get=Mock(
            return_value={
                "revision": 1,
                "default": "reject",
                "allowlisted_classes": {
                    "tools": ["command_name"],
                    "environments": ["environment_name"],
                },
                "allowlisted_values": {"tools": ["a_tool"], "environments": ["env"]},
            }
        ),
        update=Mock(),
    )
    client = SimpleNamespace(agent=SimpleNamespace(literal_policy=literal_policy))
    zep_capture.set_literal_policy(
        cast(Zep, client),
        "agent-1",
        tools=["a_tool"],
        environments=["env"],
        idempotency_prefix="run",
    )

    literal_policy.get.assert_called_once_with("agent-1")
    literal_policy.update.assert_not_called()
