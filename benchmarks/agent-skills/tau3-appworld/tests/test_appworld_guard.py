from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Self, cast

import httpx
import pytest
from data.appworld_guard_cases import CASES

import appworld_run
from appworld_guard import (
    EXPECTED_ALLOWED_MODULES,
    EXPECTED_DISALLOWED_FUNCTION_PATHS,
    GUARD_PREFIX,
    RUNTIME_GUARD_MARKERS,
    SHELL_ESCAPE_NAMES,
    check_agent_code,
    env_probe_code,
    guard_state_code,
    parse_guard_state,
    parse_secret_env_names,
)
from call_ledger import is_infrastructure_error
from redaction import Redactor


@pytest.mark.parametrize(("case_id", "group", "code", "expected"), CASES)
def test_agent_code_cases(case_id: str, group: str, code: str, expected: str) -> None:
    message = check_agent_code(code)
    assert ("blocked" if message else "allowed") == expected, case_id
    assert not message or message.startswith(GUARD_PREFIX), case_id


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "import appworld\nexec('pass')",
            "Usage of the following module is not allowed: appworld.",
        ),
        (
            "import pathlib\nprint(pathlib.Path('data/tasks').exists())",
            "Usage of the following module is not allowed: pathlib.",
        ),
        (
            "from os import getenv\nprint(__main__)",
            "Usage of the following module is not allowed: os.",
        ),
        (
            "print(object.__dict__)\ngetattr(apis, 'x')",
            "Usage of the following name is not allowed: __dict__.",
        ),
        (
            "getattr(apis, 'x')",
            "Usage of the following function is not allowed: getattr.",
        ),
        (
            "import os as o\no.environ\nimport os as later",
            "Usage of the following module is not allowed: os.",
        ),
        (
            "func = open",
            "Usage of the following function is not allowed: open.",
        ),
        ("open = None", "Usage of the following function is not allowed: open."),
    ],
)
def test_guard_rule_order_and_messages(code: str, message: str) -> None:
    assert check_agent_code(code) == GUARD_PREFIX + message


def test_guard_skips_syntax_errors_strings_comments_and_non_os_attributes() -> None:
    assert check_agent_code("def (") is None
    assert check_agent_code('print("_main_")  # __globals__') is None
    assert check_agent_code("print(apis.exec_code)") is None
    assert check_agent_code("print(open('f'))") is not None
    assert check_agent_code("import os.path as p\nprint(p.environ)") is not None


@pytest.mark.parametrize(
    "code",
    [
        "import string\ng = string.Formatter().get_field('0.__init__.__globals__', [type(apis)], {})[0]",
        "print('{0.__class__.__mro__}'.format(apis))",
        "from os import *\nprint(environ)",
        "from appworld.serve import environment",
        "import operator\nf = operator.attrgetter('__class__')\nprint(f(apis))",
        (
            "import string\nF = string.Formatter()\n"
            "w = F.get_field('0.__init__.__globals__[sys].modules[appworld.serve.environment].world', "
            "[type(apis)], {})[0]\n"
            "apis.supervisor.complete_task(answer=w.task.ground_truth.answer)\nprint('submitted')"
        ),
        (
            "import unittest.mock as m\n"
            "t = 'appworld.serve' + '.environment.world.task.ground' + '_truth'\n"
            "g = m.patch(t).get_original()[0]\nprint(g.answer)"
        ),
        (
            "import unittest.mock as m\n"
            "t = 'appworld.serve' + '.environment.world.task.ground' + '_truth'\n"
            "g = m.patch(t).get_original()[0]\n"
            "apis.supervisor.complete_task(answer=g.answer)\nprint('submitted')"
        ),
        (
            "sh = get_ipython()\n"
            "m = 'appworld.serve' + '.environ' + 'ment'\n"
            "g = 'ground' + '_truth'\n"
            "src = 'import sys' + chr(10) + 'w = sys' + '.mod' + 'ules[' + repr(m) + "
            "'].world' + chr(10) + 'ANS = w.task.' + g + '.answer'\n"
            "sh.ex(src)\n"
            "print(sh.user_ns['ANS'])\n"
            "apis.supervisor.complete_task(answer=sh.user_ns['ANS'])\n"
            "print('submitted')"
        ),
    ],
)
def test_guard_rejects_review_bypass_candidates(code: str) -> None:
    assert check_agent_code(code) is not None


@pytest.mark.parametrize("name", sorted(SHELL_ESCAPE_NAMES))
def test_guard_rejects_appworld_shell_escape_names(name: str) -> None:
    if name not in {"ex", "ev"}:
        assert check_agent_code(name) is not None
    assert check_agent_code(f"shell.{name}") is not None


@pytest.mark.parametrize(
    "code",
    [
        "try:\n    x = 1\nexcept Exception as ex:\n    print(ex)",
        "ex = 3\nprint(ex + 1)",
        "def f(ev):\n    return ev",
        "import re\np = re.compile(r'^\\d+')\nprint(p.match('12'))",
    ],
)
def test_guard_allows_ex_ev_names_and_re_compile(code: str) -> None:
    assert check_agent_code(code) is None


@pytest.mark.parametrize(
    "code",
    [
        "get_ipython().ex('1')",
        "g = apis\ng.ex('x')",
        "y.ev('1')",
        "compile('1', '<s>', 'eval')",
        "c = compile",
        "import re\nr2 = re\nr2.compile('x')",
        "apis.compile('x')",
    ],
)
def test_guard_blocks_ex_ev_attributes_and_other_compile_uses(code: str) -> None:
    assert check_agent_code(code) is not None


@pytest.mark.parametrize(
    "code",
    [
        "import string\ng = string.Formatter().get_field('0.__init__.__globals__', [type(apis)], {})[0]\nprint(list(g)[:5])",
        "print('{0.__class__.__mro__}'.format(apis))",
        "from os import *\nprint(environ)",
        "from appworld.serve import environment",
        "import operator\nf = operator.attrgetter('__class__')\nprint(f(apis))",
        "getattr(apis, 'name')",
        "setattr(apis, 'name', 'value')",
        "delattr(apis, 'name')",
        "vars(apis)",
        "globals()",
        "locals()",
        "__import__('json')",
        "eval('1')",
        "exec('pass')",
        "compile('1', 'x', 'eval')",
        "string.Formatter",
        "operator.attrgetter('name')",
        "operator.methodcaller('name')",
        "value.format_map({})",
        "value.format(x)",
        "template.format(name=name)",
        "'{0.name}'.format(x)",
        "str.format",
        "print(b'__')",
        "password = 'ab__cd'",
    ],
)
def test_guard_rejects_format_and_dynamic_access_sinks(code: str) -> None:
    assert check_agent_code(code) is not None


@pytest.mark.parametrize(
    "code",
    [
        'f"{x:.2f}"',
        "f\"{d['k']}\"",
        '"{:.2f}".format(x)',
        '"{} {}".format(a, b)',
        '"{0}".format(a)',
        "'{0[title]}'.format(song)",
        "json.dumps({'value': x})",
        "[value for value in values]",
        "apis.amazon.search(query='coffee')",
        "apis.supervisor.complete_task(answer=1)",
    ],
)
def test_guard_accepts_ordinary_agent_code(code: str) -> None:
    assert check_agent_code(code) is None


@pytest.mark.parametrize(
    "code",
    [
        'apis.spotify.search(query="open road")',
        'print("help")',
    ],
)
def test_runtime_guard_allows_generic_words_in_strings(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    requests: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"output": "ran"})
        )
    )
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    server = appworld_run.AppWorldServer("http://localhost:9101")

    output = server.call("execute", task_id="task_1", code=code)

    assert output == "ran"
    assert len(requests) == 1
    http.close()


@pytest.mark.parametrize(
    "code",
    [
        "safety_guard = None",
        "del safety_guard",
        "target.safety_guard = None",
        "del target.safety_guard",
        "global safety_guard",
        "def safety_guard():\n    pass",
        "class safety_guard:\n    pass",
        "def outer():\n    safety_guard = None\n    def inner():\n        nonlocal safety_guard",
    ],
)
def test_guard_blocks_writes_to_safety_guard(code: str) -> None:
    assert check_agent_code(code) == (
        GUARD_PREFIX + "Usage of the following name is not allowed: safety_guard."
    )


def test_blocked_execute_returns_without_an_http_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"output": "ran"})
        )
    )
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    server = appworld_run.AppWorldServer("http://localhost:9101")

    output = server.call("execute", task_id="task_1", code="import appworld")

    assert output.startswith(GUARD_PREFIX)
    assert requests == []
    assert server.blocked_codes == 1
    server.begin_episode()
    assert server.blocked_codes == 0
    http.close()


def test_runtime_markers_return_a_guard_message_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"output": "ran"})
        )
    )
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    server = appworld_run.AppWorldServer("http://localhost:9101")
    code = "# compare with ground_truth later\nprint(1)"

    output = server.call("execute", task_id="task_1", code=code)

    assert output.startswith(GUARD_PREFIX)
    assert server.guard_marker_hits == 1
    assert server.guard_marker_code == [code]
    assert server.blocked_codes == 1
    result = appworld_run.AppWorldResult("appworld", "task_1", 0, 1)
    appworld_run._record_guard_markers(result, server)
    assert result.guard_marker_hits == 1
    assert result.guard_marker_code == [code]
    assert requests == []
    http.close()


def test_runtime_guard_stores_redacted_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"output": "ran"}, request=request)
        )
    )
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    server = appworld_run.AppWorldServer("http://localhost:9101")
    token = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    redactor = Redactor.from_values(
        {
            "email": "supervisor@example.com",
            "phone": "555-0100",
            "auth_token": token,
        }
    )
    server.redactor = redactor
    code = f"# ground_truth contains supervisor@example.com, 555-0100, Bearer {token}\nprint(1)"

    server.call("execute", task_id="task_1", code=code)

    stored_code = server.guard_marker_code[0]
    assert code not in stored_code
    assert "supervisor@example.com" not in stored_code
    assert "555-0100" not in stored_code
    assert token not in stored_code
    result = appworld_run.AppWorldResult("appworld", "task_1", 0, 1)
    appworld_run._record_guard_markers(result, server, redactor)
    assert result.guard_marker_code == [stored_code]
    http.close()


@pytest.mark.parametrize(
    "marker",
    RUNTIME_GUARD_MARKERS,
)
def test_runtime_markers_are_case_insensitive_and_cover_comments(
    monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    requests: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"output": "ran"})
        )
    )
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    server = appworld_run.AppWorldServer("http://localhost:9101")
    code = f"# {marker.upper()}\nprint(1)"

    output = server._send("execute", {"task_id": "task_1", "code": code})

    assert output.startswith(GUARD_PREFIX)
    assert server.guard_marker_hits == 1
    assert server.guard_marker_code == [code]
    assert requests == []
    http.close()


def test_runtime_marker_rows_are_counted_by_arm() -> None:
    summary = appworld_run.summarize(
        [
            {
                "task_id": "t1",
                "trial": 1,
                "arm": "skills",
                "reward": 1.0,
                "guard_marker_hits": 1,
            },
            {
                "task_id": "t2",
                "trial": 1,
                "arm": "skills",
                "reward": 1.0,
                "guard_marker_hits": 0,
            },
            {
                "task_id": "t3",
                "trial": 1,
                "arm": None,
                "reward": 0.0,
                "guard_marker_hits": 2,
            },
        ]
    )

    assert summary["guard_marker_rows_by_arm"] == {"none": 1, "skills": 1}


def test_runtime_guard_returns_marker_message_from_direct_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"output": "ran"})
        )
    )
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    server = appworld_run.AppWorldServer("http://localhost:9101")
    code = "print(__globals__)"

    output = server._send("execute", {"task_id": "task_1", "code": code})

    assert output.startswith(GUARD_PREFIX)
    result = appworld_run.AppWorldResult(
        "appworld", "task_1", 0, 1, guard_marker_hits=server.guard_marker_hits
    )
    assert appworld_run._stage_stop_reason(result) is None
    assert requests == []
    http.close()


def test_runtime_marker_episode_continues_and_is_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Server(appworld_run.AppWorldServer):
        def __init__(self) -> None:
            super().__init__("http://localhost:9101")
            self.completed = False

        def verify_guard_state(self, _task_id: str) -> float:
            return 0.0

        def call(self, method: str, **body: Any) -> Any:
            if method == "initialize":
                return {
                    "instruction": "Complete the task.",
                    "supervisor": {
                        "first_name": "Alex",
                        "last_name": "Example",
                        "email": "alex@example.test",
                        "phone_number": "555-0100",
                    },
                }
            if method == "execute":
                code = str(body["code"])
                if "ground_truth" in code:
                    return super().call(method, **body)
                message = check_agent_code(code)
                if message:
                    return message
                self.completed = True
                return "submitted"
            if method == "task_completed":
                return self.completed
            if method == "evaluate":
                return {"success": True, "passes": [{}], "failures": [], "num_tests": 1}
            return None

    class Recorder:
        trajectory_uuid = "trace-1"

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def emit(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def record_message(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def record_tool_call(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def record_tool_result(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def set_result(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    def response(call_id: str, code: str) -> SimpleNamespace:
        return SimpleNamespace(
            output=[
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": "execute_python",
                    "arguments": json.dumps({"code": code}),
                }
            ],
            output_text="",
            usage=SimpleNamespace(
                input_tokens=2,
                input_tokens_details=SimpleNamespace(cached_tokens=0, cache_creation_tokens=0),
                output_tokens=1,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            stop_reason=None,
            incomplete_details=None,
        )

    responses = iter(
        [
            response("marker", "# compare with ground_truth\nprint(1)"),
            response("complete", "apis.supervisor.complete_task(answer='done')"),
        ]
    )
    http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    monkeypatch.setattr(appworld_run, "TrajectoryRecorder", Recorder)
    monkeypatch.setattr(appworld_run, "_completion", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(appworld_run.TRACER, "start_trace", lambda *_a, **_k: "trace")
    monkeypatch.setattr(appworld_run.TRACER, "end_trace", lambda *_a, **_k: None)
    server = Server()

    result, _ = appworld_run.run_episode(
        server=server,
        client=SimpleNamespace(),
        openai_client=SimpleNamespace(),
        agent_uuid="agent",
        stage="teacher",
        run_id="run",
        task_id="task_1",
        task_index=0,
        trial=1,
        model="gpt-6-luna",
        reasoning_effort="low",
        max_steps=3,
        arm="none",
        memory_agent_uuid=None,
        corpus=None,
        curator_model="gpt-6-luna",
        curator_reasoning_effort="medium",
    )

    assert result.guard_marker_hits == 1
    assert result.guard_marker_code == ["# compare with ground_truth\nprint(1)"]
    assert result.completed and result.reward == 1.0
    assert result.error is None
    assert appworld_run._stage_stop_reason(result) is None
    http.close()


def test_guard_state_parser_is_stable_and_detects_a_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = parse_guard_state(_guard_state_output(guard_fnv=12))
    second = parse_guard_state(_guard_state_output(guard_fnv=12))
    changed = parse_guard_state(_guard_state_output(guard_fnv=13))
    assert first == second and first != changed

    server = appworld_run.AppWorldServer("http://localhost:9102")
    server.frozen_guard_state = first
    monkeypatch.setattr(
        server,
        "_probe",
        lambda _task_id, _code: _guard_state_output(guard_fnv=13),
    )
    with pytest.raises(appworld_run.AppWorldServerStateError, match="(?i)restart"):
        server.verify_guard_state("task_1")
    server.http.close()


def test_environment_probe_returns_names_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPWORLD_SECRET_TEST", "secret-value-123")
    monkeypatch.setenv("UNRELATED_SETTING", "not-secret")
    probe = subprocess.run(
        [sys.executable, "-c", env_probe_code()],
        check=True,
        capture_output=True,
        text=True,
    )
    output = probe.stdout
    names = parse_secret_env_names(output)
    assert "APPWORLD_SECRET_TEST" in names
    assert "secret-value-123" not in output
    assert "not-secret" not in output


def _guard_state_output(
    *,
    guard_fnv: int = 1,
    allowed: set[str] | frozenset[str] = EXPECTED_ALLOWED_MODULES,
    disallowed: set[str] | frozenset[str] = EXPECTED_DISALLOWED_FUNCTION_PATHS,
) -> str:
    state = {
        "allowed": sorted(allowed),
        "disallowed": sorted(disallowed),
        "guard_fnv": guard_fnv,
        "guard_len": 1,
        "parsing_fnv": 1,
        "parsing_len": 1,
    }
    return "APPWORLD_GUARD_STATE " + json.dumps(state, sort_keys=True)


def _http_5xx(url: str = "http://localhost:9101/execute") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    return httpx.HTTPStatusError(
        "server error", request=request, response=httpx.Response(503, request=request)
    )


@pytest.mark.parametrize(
    ("health_result", "expected_type", "is_infrastructure"),
    [
        ("passed", "agent_broke_environment", False),
        ("failed", "appworld_server_error", True),
    ],
)
def test_5xx_health_probe_classifies_and_controls_resume(
    health_result: str, expected_type: str, is_infrastructure: bool
) -> None:
    result = appworld_run.AppWorldResult(
        domain="appworld",
        task_id="task_1",
        task_index=0,
        trial=1,
        arm=None,
    )
    server = SimpleNamespace(
        url="http://localhost:9101",
        health_probe=lambda *_args: health_result,
    )

    appworld_run._record_episode_error(result, cast(Any, server), "task_1", "run_1", _http_5xx())

    assert result.error_type == expected_type
    assert result.health_probe == health_result
    assert is_infrastructure_error(result.error, result.error_detail, result.error_type) is (
        is_infrastructure
    )


def test_zep_5xx_is_not_classified_as_an_appworld_error() -> None:
    result = appworld_run.AppWorldResult(
        domain="appworld",
        task_id="task_1",
        task_index=0,
        trial=1,
        arm=None,
    )
    health_probes: list[tuple[Any, ...]] = []
    server = SimpleNamespace(
        url="http://localhost:9101",
        health_probe=lambda *args: health_probes.append(args) or "passed",
    )

    appworld_run._record_episode_error(
        result,
        cast(Any, server),
        "task_1",
        "run_1",
        _http_5xx("https://api.example.test/api/v4/agents"),
    )

    assert result.error_type is None
    assert result.health_probe is None
    assert health_probes == []
    assert is_infrastructure_error(result.error, result.error_detail, result.error_type)


def _mock_preflight_server(
    monkeypatch: pytest.MonkeyPatch,
    star_status: int,
    guard_output: str | None = None,
    os_star_status: int = 200,
    os_star_output: str = (
        "Execution failed. Traceback:\nUsage of the following function is not allowed: os.system."
    ),
) -> tuple[appworld_run.AppWorldServer, list[str], list[str]]:
    request_codes: list[str] = []
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path.endswith("/execute"):
            code = str(body["code"])
            request_codes.append(code)
            if code == "from math import *":
                return httpx.Response(star_status, json={"output": ""}, request=request)
            if code == "from os import *\nsystem('true')":
                return httpx.Response(
                    os_star_status, json={"output": os_star_output}, request=request
                )
            if code == guard_state_code():
                output = guard_output or _guard_state_output()
            else:
                output = "APPWORLD_ENV_NAMES []"
            return httpx.Response(200, json={"output": output}, request=request)
        return httpx.Response(200, json={"output": ""}, request=request)

    http = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(appworld_run, "ledger_client", lambda *_args, **_kwargs: http)
    send = cast(Any, appworld_run.AppWorldServer._send)
    monkeypatch.setattr(send.retry, "wait", lambda _state: 0)
    preflight_send = cast(Any, appworld_run.AppWorldServer._send_preflight_execute)
    monkeypatch.setattr(preflight_send.retry, "wait", lambda _state: 0)
    server = appworld_run.AppWorldServer("http://appworld.test:9101")
    return server, request_codes, paths


def test_preflight_stops_when_star_import_patch_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, request_codes, paths = _mock_preflight_server(monkeypatch, 500)
    try:
        with pytest.raises(appworld_run.AppWorldServerPatchError) as error:
            server.preflight("task_1", "run_1")
        message = str(error.value)
        assert server.url in message
        assert "appworld/common/code_parsing.py" in message
        assert "parse_code_function_paths" in message
        assert "patch -p0 -i" in message
        assert request_codes.count("from math import *") == 4
        assert paths[-1] == "/close"
    finally:
        server.http.close()


def test_preflight_fails_when_os_star_import_probe_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, request_codes, paths = _mock_preflight_server(
        monkeypatch,
        200,
        os_star_output="Execution successful.",
    )
    try:
        with pytest.raises(appworld_run.AppWorldServerPatchError):
            server.preflight("task_1", "run_1")
        assert request_codes.count("from math import *") == 1
        assert request_codes.count("from os import *\nsystem('true')") == 1
        assert paths[-1] == "/close"
    finally:
        server.http.close()


def test_preflight_retries_server_errors_for_os_star_import_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, request_codes, paths = _mock_preflight_server(
        monkeypatch,
        200,
        os_star_status=500,
    )
    try:
        with pytest.raises(appworld_run.AppWorldServerPatchError):
            server.preflight("task_1", "run_1")
        assert request_codes.count("from math import *") == 1
        assert request_codes.count("from os import *\nsystem('true')") == 4
        assert paths[-1] == "/close"
    finally:
        server.http.close()


def test_preflight_passes_when_appworld_guard_blocks_os_system_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_guard_message = (
        "Execution failed. Traceback:\nUsage of the following function is not allowed: os.system."
    )
    server, request_codes, paths = _mock_preflight_server(
        monkeypatch,
        200,
        os_star_output=native_guard_message,
    )
    try:
        assert server.preflight("task_1", "run_1") == []
        assert request_codes.count("from math import *") == 1
        assert request_codes.count("from os import *\nsystem('true')") == 1
        assert paths[-1] == "/close"
    finally:
        server.http.close()


@pytest.mark.parametrize(
    ("allowed", "disallowed"),
    [
        (EXPECTED_ALLOWED_MODULES - {"math"}, EXPECTED_DISALLOWED_FUNCTION_PATHS),
        (EXPECTED_ALLOWED_MODULES, EXPECTED_DISALLOWED_FUNCTION_PATHS - {"os.system"}),
    ],
)
def test_preflight_rejects_changed_native_guard_sets(
    monkeypatch: pytest.MonkeyPatch,
    allowed: frozenset[str],
    disallowed: frozenset[str],
) -> None:
    server, _request_codes, paths = _mock_preflight_server(
        monkeypatch,
        200,
        _guard_state_output(allowed=allowed, disallowed=disallowed),
    )
    try:
        with pytest.raises(appworld_run.AppWorldServerStateError, match="Restart"):
            server.preflight("task_1", "run_1")
        assert paths == ["/initialize", "/execute", "/close"]
    finally:
        server.http.close()


def test_verify_guard_state_lazily_freezes_then_rejects_a_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = appworld_run.AppWorldServer("http://localhost:9102")
    outputs = iter([_guard_state_output(guard_fnv=12), _guard_state_output(guard_fnv=13)])
    monkeypatch.setattr(server, "_probe", lambda _task_id, _code: next(outputs))
    try:
        server.verify_guard_state("task_1")
        assert server.frozen_guard_state == parse_guard_state(_guard_state_output(guard_fnv=12))
        with pytest.raises(appworld_run.AppWorldServerStateError, match="Restart"):
            server.verify_guard_state("task_1")
    finally:
        server.http.close()


def test_legacy_rows_keep_their_infrastructure_classification() -> None:
    assert is_infrastructure_error("HTTPStatusError", "HTTPStatusError:503")
    assert not is_infrastructure_error(
        "HTTPStatusError", "HTTPStatusError:503", "agent_broke_environment"
    )
    assert is_infrastructure_error(
        "AppWorldServerStateError", "server state changed", "server_state_changed"
    )


def test_resume_does_not_retry_a_broken_world_but_retries_infrastructure_rows() -> None:
    import run

    rows = [
        {
            "domain": "appworld",
            "task_index": 0,
            "trial": 1,
            "error": "HTTPStatusError",
            "error_detail": "HTTPStatusError:503",
            "error_type": "agent_broke_environment",
        },
        {
            "domain": "appworld",
            "task_index": 1,
            "trial": 1,
            "error": "HTTPStatusError",
            "error_detail": "HTTPStatusError:503",
            "error_type": "appworld_server_error",
        },
        {
            "domain": "appworld",
            "task_index": 2,
            "trial": 1,
            "error": "HTTPStatusError",
            "error_detail": "HTTPStatusError:503",
        },
    ]
    completed = run._completed_keys(rows)
    assert ("appworld", 0, 1) in completed
    assert ("appworld", 1, 1) not in completed
    assert ("appworld", 2, 1) not in completed


def test_health_probe_reinitializes_executes_and_closes() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    server = appworld_run.AppWorldServer("http://localhost:9101")

    def call(method: str, **body: Any) -> Any:
        calls.append((method, body))
        return None

    server.call = call  # type: ignore[method-assign]
    assert server.health_probe("task_1", "run_1") == "passed"
    assert [method for method, _ in calls] == [
        "close",
        "initialize",
        "execute",
        "evaluate",
        "close",
    ]
    assert calls[2][1]["code"] == "print(1)"
    server.http.close()


def test_health_probe_evaluate_failure_stays_an_infrastructure_error() -> None:
    server = appworld_run.AppWorldServer("http://localhost:9101")

    def call(method: str, **_body: Any) -> Any:
        if method == "evaluate":
            raise _http_5xx(f"{server.url}/evaluate")
        return None

    server.call = call  # type: ignore[method-assign]
    result = appworld_run.AppWorldResult(
        domain="appworld",
        task_id="task_1",
        task_index=0,
        trial=1,
        arm=None,
    )
    try:
        appworld_run._record_episode_error(result, server, "task_1", "run_1", _http_5xx())
        assert result.health_probe == "failed"
        assert result.error_type == "appworld_server_error"
        assert is_infrastructure_error(result.error, result.error_detail, result.error_type)
    finally:
        server.http.close()
