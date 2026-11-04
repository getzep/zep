import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from zep_skills import TraceRecorder, ZepSkills, readable_skills


def test_trace_and_skill_use_sdk_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.test")
    requests: list[tuple[str, str, dict[str, Any], httpx.Headers]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body, request.headers))
        path = request.url.path
        if request.method == "POST" and path.endswith("/agents"):
            response = {"uuid": "agent-uuid"}
        elif path.endswith("/traces/trace-uuid/close"):
            response = {"task": {"uuid": "task-uuid"}, "trace": {"uuid": "trace-uuid"}}
        elif path.endswith("/tasks/task-uuid"):
            response = {"uuid": "task-uuid", "status": "succeeded"}
        elif path.endswith("/traces/trace-uuid"):
            response = {
                "uuid": "trace-uuid",
                "status": "closed",
                "verification": "external",
            }
        elif path.endswith("/uses/skill-use-uuid/outcome"):
            response = {"uuid": "outcome-uuid"}
        elif path.endswith("/skills/skill-uuid/uses"):
            response = {"uuid": "skill-use-uuid"}
        elif path.endswith("/traces"):
            response = {"uuid": "trace-uuid", "status": "open"}
        else:
            response = {"uuid": "event-uuid"}
        return httpx.Response(200, json=response)

    zep = ZepSkills(
        tmp_path / "zep-calls.jsonl",
        "langgraph",
        transport=httpx.MockTransport(handle),
    )
    try:
        agent_data = zep.create_agent()
        recorder = TraceRecorder(zep, agent_data["uuid"])
        assert recorder.start() == "trace-uuid"
        recorder.append(
            "tool_call",
            '{"document_id":"monthly-interest"}',
            "read_document",
            "call-1",
        )
        closed = recorder.close(True)
        assert closed["trace_uuid"] == "trace-uuid"
        assert closed["final_trace"]["verification"] == "external"
        assert closed["event_count"] == 1
        use = zep.record_skill_use(
            "agent-uuid",
            "skill-uuid",
            "search-uuid",
            "trace-uuid",
            used=True,
            passed=True,
        )
    finally:
        zep.close()

    assert agent_data["uuid"] == "agent-uuid"
    assert use["uuid"] == "skill-use-uuid"
    assert [(method, path.split("/api/v4")[-1]) for method, path, _, _ in requests] == [
        ("POST", "/agents"),
        ("POST", "/agents/agent-uuid/traces"),
        ("POST", "/agents/agent-uuid/traces/trace-uuid/events"),
        ("POST", "/agents/agent-uuid/traces/trace-uuid/close"),
        ("GET", "/tasks/task-uuid"),
        ("GET", "/agents/agent-uuid/traces/trace-uuid"),
        ("POST", "/agents/agent-uuid/skills/skill-uuid/uses"),
        ("POST", "/agents/agent-uuid/skills/skill-uuid/uses/skill-use-uuid/outcome"),
    ]
    agent_body = requests[0][2]
    assert str(agent_body["agent_id"]).startswith("frameworks-banking-langgraph-")
    settings = agent_body["memory_settings"]
    assert settings["approval"] == "auto"
    assert settings["auto_approval_task_families"] == ["banking.synthetic-monthly-correction"]
    assert settings["compilation_mode"] == "on_close"
    create = requests[1][2]
    assert create["objective"]
    assert create["task_family"] == "banking.synthetic-monthly-correction"
    assert create["metadata"] == {
        "source": "agent-skills-frameworks",
        "framework": "langgraph",
        "data": "invented",
    }
    event = requests[2][2]
    assert event["event_type"] == "tool_call"
    assert event["content"] == '{"document_id":"monthly-interest"}'
    assert event["tool_call_id"] == "call-1"
    assert event["context"] == {"tools": [{"name": "read_document"}]}
    for _, _, _, headers in requests[:4] + requests[6:]:
        key = headers["Idempotency-Key"]
        assert str(uuid.UUID(key)) == key
        assert uuid.UUID(key).version == 4
    close_body = requests[3][2]
    assert close_body["outcome"] == "succeeded"
    assert close_body["verification"] == "external"
    assert close_body["verifier"] == {
        "verifier_id": "frameworks-banking-state",
        "class": "harness",
        "outcome": "succeeded",
        "evidence_reference": "trace-uuid",
    }
    use_body = requests[6][2]
    assert use_body == {
        "search_id": "search-uuid",
        "trace_uuid": "trace-uuid",
        "usage": "used",
    }
    assert requests[7][2] == {"outcome": "succeeded"}
    ledger = [json.loads(line) for line in (tmp_path / "zep-calls.jsonl").read_text().splitlines()]
    assert all(set(item) == {"method", "path", "status"} for item in ledger)
    assert len(ledger) == len(requests)
    assert all(item["status"] == 200 for item in ledger)


def test_readable_skills_requires_a_version_and_markdown() -> None:
    skills = readable_skills(
        {
            "items": [
                {"uuid": "skill-1", "version": 1, "markdown": "# Skill"},
                {"uuid": "skill-2", "version": 2, "markdown": ""},
                {"uuid": "skill-3", "version": "3", "markdown": "# Skill"},
                {"version": 4, "markdown": "# Skill"},
            ]
        }
    )
    assert [skill["uuid"] for skill in skills] == ["skill-1"]
