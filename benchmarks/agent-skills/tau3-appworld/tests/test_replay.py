from typing import Any

import pytest

from replay import replay_events


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []
        self.tool_call_ids: list[str | None] = []
        self.tool_result_ids: list[str | None] = []

    def record_message(self, role: str, content: str) -> None:
        self.events.append(("message", role, content))

    def record_tool_call(self, name: str, arguments: Any, *, call_id: str | None = None) -> None:
        self.events.append(("tool_call", name, str(arguments)))
        self.tool_call_ids.append(call_id)

    def record_tool_result(self, name: str, result: Any, *, call_id: str | None = None) -> None:
        self.events.append(("tool_result", name, str(result)))
        self.tool_result_ids.append(call_id)


def test_replay_records_user_device_calls_as_user_observations() -> None:
    recorder = Recorder()
    replay_events(
        recorder,  # type: ignore[arg-type]
        [
            {"role": "assistant", "tool_calls": [{"id": "a1", "name": "get_details"}]},
            {"role": "tool", "id": "a1", "content": "line active"},
            {"role": "user", "tool_calls": [{"id": "u1", "name": "run_speed_test"}]},
            {"role": "tool", "id": "u1", "content": "slow"},
        ],
    )

    assert recorder.events == [
        ("tool_call", "get_details", "{}"),
        ("tool_result", "get_details", "line active"),
        ("message", "user", "device run_speed_test result: slow"),
    ]
    assert recorder.tool_call_ids == ["a1"]
    assert recorder.tool_result_ids == ["a1"]


def test_replay_rejects_a_result_without_a_call() -> None:
    with pytest.raises(ValueError, match="no matching tool call"):
        replay_events(Recorder(), [{"role": "tool", "id": "x", "content": "value"}])  # type: ignore[arg-type]
