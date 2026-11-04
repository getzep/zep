from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import audit_code
from appworld_guard import GUARD_PREFIX


def _call(call_id: str, name: str, code: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "name": name,
        "arguments": {"code": code},
    }


def test_audit_matches_only_guarded_ex_ev_and_compile_names() -> None:
    allowed_cases = [
        ("try:\n    pass\nexcept Exception as ex:\n    print(ex)", "name:ex"),
        ("def f(ev):\n    return ev", "name:ev"),
        ("import re\np = re.compile('x')", "name:compile"),
    ]
    for code, pattern in allowed_cases:
        assert pattern not in audit_code._matching_patterns(code)

    blocked_cases = [
        ("get_ipython().ex(s)", {"name:ex", "name:get_ipython"}),
        ("y.ev('1')", {"name:ev"}),
        ("shell . ex(source)", {"name:ex"}),
        ("shell . ev(source)", {"name:ev"}),
        ("compile('1', '', 'eval')", {"name:compile"}),
        ("apis.compile('x')", {"name:compile"}),
        ("are.compile('x')", {"name:compile"}),
        ("obj.re.compile('x')", {"name:compile"}),
        (
            "import re\np = re.compile('x')\nq = 'compile'",
            {"name:compile"},
        ),
        ("except Exception as ex print(ex", {"name:ex"}),
    ]
    for code, patterns in blocked_cases:
        assert patterns <= set(audit_code._matching_patterns(code))


def test_audit_does_not_count_allowed_names_as_executed_matches(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    task_id = "appworld_task_1"
    calls = [
        _call(
            "call-1",
            "execute_python",
            "try:\n    pass\nexcept Exception as ex:\n    print(ex)",
        ),
        _call("call-2", "execute_python", "def f(ev):\n    return ev"),
        _call("call-3", "execute_python", "import re\np = re.compile('x')"),
    ]
    messages = [
        {"role": "assistant", "tool_calls": calls},
        *[{"role": "tool", "id": call["id"], "content": "Executed."} for call in calls],
    ]
    row = {"task_id": task_id, "trial": 0, "arm": "r-skill"}
    (run_dir / "heldout-r-skill.jsonl").write_text(json.dumps(row) + "\n")
    (run_dir / "transcripts-heldout-r-skill.jsonl").write_text(
        json.dumps({"task_id": task_id, "messages": messages}) + "\n"
    )

    summary = audit_code.audit_run_directory(run_dir)
    arm = summary["by_arm"]["r-skill"]

    assert arm["executed_blocks"] == 3
    assert arm["matching_blocks"] == 0
    assert arm["matching_executed_blocks"] == 0


def test_audit_reports_blocked_and_executed_blocks_by_arm_and_task(
    tmp_path: Path, capsys: Any
) -> None:
    run_dir = tmp_path / "run"
    simulations = run_dir / "simulations"
    simulations.mkdir(parents=True)
    task_id = "appworld_task_1"
    executed_messages = [
        {
            "role": "assistant",
            "tool_calls": [
                _call(
                    "call-1",
                    "execute_python",
                    "shell = get_ipython()\n"
                    "shell.ex(src)\n"
                    "print('ground_truth __globals__')\n"
                    "exec(src)\n"
                    "eval('1')\n"
                    "compile('1', 'x', 'eval')\n"
                    "open('x')",
                ),
                _call("call-2", "execute_python", "apis.supervisor.complete_task(answer=1)"),
                _call("call-3", "read_skill", "print('not executed')"),
            ],
        },
        {
            "role": "tool",
            "id": "call-1",
            "content": f"{GUARD_PREFIX}Usage of the following code marker is not allowed: get_ipython.",
        },
        {"role": "tool", "id": "call-2", "content": "Task completed."},
    ]
    result_row = {"task_id": task_id, "trial": 0, "arm": "r-skill"}
    (run_dir / "heldout-r-skill.jsonl").write_text(json.dumps(result_row) + "\n")
    (run_dir / "transcripts-heldout-r-skill.jsonl").write_text(
        json.dumps({"task_id": task_id, "messages": executed_messages}) + "\n"
    )
    (simulations / "heldout-r-skill-appworld-0-0.json").write_text(
        json.dumps({"task_id": task_id, "messages": executed_messages})
    )

    summary = audit_code.audit_run_directory(run_dir)

    assert summary["blocks_scanned"] == 2
    assert summary["executed_blocks"] == 1
    assert summary["blocked_blocks"] == 1
    assert summary["matching_blocks"] == 1
    task = summary["by_arm"]["r-skill"]["tasks"][task_id]
    assert task["blocks_scanned"] == 2
    assert task["executed_blocks"] == 1
    assert task["blocked_blocks"] == 1
    assert task["matching_blocks"] == 1
    match = task["matching_blocks_detail"][0]
    assert match["call_id"] == "call-1"
    assert match["execution_status"] == "blocked"
    assert "name:get_ipython" in match["patterns"]
    assert "name:ex" in match["patterns"]
    assert "name:exec" in match["patterns"]
    assert "name:eval" in match["patterns"]
    assert "name:compile" in match["patterns"]
    assert "marker:.ex(" in match["patterns"]
    assert "substring:truth" in match["patterns"]
    assert "substring:globals" in match["patterns"]
    assert "substring:__" in match["patterns"]
    assert "call:open(" in match["patterns"]
    assert len(match["sources"]) == 2
    arm = summary["by_arm"]["r-skill"]
    assert arm["executed_blocks"] == 1
    assert arm["blocked_blocks"] == 1
    assert arm["matching_executed_blocks"] == 0
    assert arm["matching_blocked_blocks"] == 1

    assert audit_code.main([str(run_dir)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["matching_blocks"] == 1
    assert output["executed_blocks"] == 1
    assert output["blocked_blocks"] == 1
