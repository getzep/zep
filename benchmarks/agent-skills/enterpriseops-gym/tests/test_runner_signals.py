from __future__ import annotations

import contextlib
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import BinaryIO

import pytest

from eog.runner import RunManifest, RunStore


def _read_events(pipe: BinaryIO, count: int, timeout_ms: int = 10000) -> bytes:
    poller = select.poll()
    poller.register(pipe, select.POLLIN)
    output = bytearray()
    while len(output) < count:
        assert poller.poll(timeout_ms)
        output.extend(pipe.read(count - len(output)))
    return bytes(output)


def _wait_for_exit(pid: int, timeout_ms: int = 10000) -> bool:
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
        return bool(poller.poll(timeout_ms))
    finally:
        os.close(pidfd)


def _fake_signal_driver(root: Path, driver: Path) -> None:
    driver.write_text(
        """
import signal
import sys
import threading
from pathlib import Path

from eog.run import _install_run_signal_handlers
from eog.runner import RunStore, run_concurrent

root = Path(sys.argv[1])
double_signal = sys.argv[2] == "1"
events = (root / "events").open("wb", buffering=0)
events.write(b"R")
with (root / "install-handler").open("rb") as pipe:
    pipe.read(1)
signals = _install_run_signal_handlers()
store = RunStore(root / "run")
rows = [
    {"task_id": str(index), "trial": 1, "arm": "none", "model": "fake", "effort": "low", "stage": "fake"}
    for index in range(6)
]

def execute(row, abort_event):
    task_id = row["task_id"]
    events.write(b"S")
    if task_id in {"0", "1"}:
        signals.stop_requested.wait()
        if double_signal:
            events.write(b"F")
            signals.abort_requested.wait()
    status = "stopped" if signals.abort_requested.is_set() else "valid"
    result = {**row, "status": status}
    if status == "stopped":
        result["stop_reason"] = "signal_abort"
    store.checkpoint(result)
    return result

signals.scheduling_started = True
run_concurrent(
    rows,
    lambda row: execute(row, signals.abort_requested),
    ramp=(2,),
    checkpoint=store.checkpoint,
    stop_requested=signals.stop_requested,
    abort_requested=signals.abort_requested,
    signal_name=lambda: signals.signal_name,
    execute_with_abort=execute,
)
print(f"exit_code={signals.exit_code}", flush=True)
events.write(b"E")
events.close()
sys.exit(signals.exit_code)
"""
    )


def _start_signal_driver(tmp_path: Path, double_signal: bool) -> tuple[int, Path, BinaryIO]:
    driver = tmp_path / "driver.py"
    _fake_signal_driver(tmp_path, driver)
    for fifo_name in ("events", "install-handler"):
        os.mkfifo(tmp_path / fifo_name)
    log_path = tmp_path / "driver.log"
    command = (
        f"cd {shlex.quote(str(tmp_path))}; "
        f"nohup {shlex.quote(sys.executable)} {shlex.quote(str(driver))} "
        f"{shlex.quote(str(tmp_path))} {int(double_signal)} "
        f"> {shlex.quote(str(log_path))} 2>&1 < /dev/null & echo $!"
    )
    env = os.environ.copy()
    project_root = Path(__file__).parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(project_root / "src"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    completed = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return int(completed.stdout.strip()), log_path, (tmp_path / "events").open("rb", buffering=0)


def _fake_setup_signal_driver(root: Path, driver: Path) -> None:
    driver.write_text(
        """
import signal
import sys
import time
from pathlib import Path

from eog import run as run_module

root = Path(sys.argv[1])
events = (root / "events").open("wb", buffering=0)
status = Path("/proc/self/status").read_text()
sig_ign = int(next(line.split()[1] for line in status.splitlines() if line.startswith("SigIgn:")), 16)
events.write(b"I" if sig_ign & (1 << (signal.SIGINT - 1)) else b"X")

def setup(_signals):
    events.write(b"S")
    time.sleep(30)
    events.write(b"R")

run_module._run_main = setup
try:
    run_module.main()
except SystemExit as error:
    print(f"exit_code={error.code}", flush=True)
    events.write(b"E")
    events.close()
    raise
"""
    )


def _start_setup_signal_driver(tmp_path: Path) -> tuple[int, Path, BinaryIO]:
    driver = tmp_path / "setup-driver.py"
    _fake_setup_signal_driver(tmp_path, driver)
    os.mkfifo(tmp_path / "events")
    log_path = tmp_path / "setup-driver.log"
    command = (
        f"cd {shlex.quote(str(tmp_path))}; "
        f"nohup {shlex.quote(sys.executable)} {shlex.quote(str(driver))} "
        f"{shlex.quote(str(tmp_path))} > {shlex.quote(str(log_path))} 2>&1 < /dev/null & echo $!"
    )
    env = os.environ.copy()
    project_root = Path(__file__).parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(project_root / "src"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    completed = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return int(completed.stdout.strip()), log_path, (tmp_path / "events").open("rb", buffering=0)


def _fake_agent_resume_driver(root: Path, driver: Path) -> None:
    driver.write_text(
        """
import os
import time
from pathlib import Path
from types import SimpleNamespace

from eog import run as run_module
from eog import runner as runner_module
from eog.runner import RunManifest

run_root = Path(os.environ["FAKE_RUN_ROOT"])
calls_path = Path(os.environ["FAKE_AGENT_CALLS"])

class FakeAgentAPI:
    def create(self, *, agent_id, **_kwargs):
        existing = calls_path.read_text().splitlines() if calls_path.exists() else []
        with calls_path.open("a") as output:
            output.write(agent_id + "\\n")
        if agent_id in existing:
            raise RuntimeError("HTTP 409: Agent ID already exists")
        return SimpleNamespace(uuid="fake-agent-uuid")

class FakeZep:
    def __init__(self):
        self.agent = FakeAgentAPI()

def image_digest(container):
    if os.environ["FAKE_IMAGE_HANG"] == "1":
        with Path(os.environ["FAKE_IMAGE_EVENTS"]).open("wb", buffering=0) as events:
            events.write(b"D")
        time.sleep(30)
    return f"fake:{container}"

def run_concurrent(rows, execute, **_kwargs):
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "scheduler-reached").write_text("yes\\n")
    (run_root / "rows.jsonl").touch()
    return []

run_module._require_zep_environment = lambda _stage: None
run_module.list_learning_run_ids = lambda *_args, **_kwargs: {}
run_module.zep_client = FakeZep
run_module.image_digest = image_digest
run_module.freeze_manifest = lambda **kwargs: RunManifest(
    git_commit="test",
    dataset={},
    image_digests=kwargs["image_digests"],
    split_sha256="test",
    skills_snapshot_sha256="",
    price_table_sha256="test",
)
runner_module.run_concurrent = run_concurrent

try:
    run_module.main()
except SystemExit as error:
    print(f"exit_code={error.code}", flush=True)
    raise
else:
    print("exit_code=0", flush=True)
"""
    )


def _start_agent_resume_driver(
    tmp_path: Path,
    *,
    resume: bool,
) -> tuple[subprocess.Popen[str], int, Path]:
    driver = tmp_path / "agent-resume-driver.py"
    if not driver.exists():
        _fake_agent_resume_driver(tmp_path, driver)
    run_id = "f1-agent-resume"
    run_root = tmp_path / "runs" / run_id
    log_path = tmp_path / f"agent-resume-{resume}.log"
    noop_baseline = tmp_path / "noop-baseline.jsonl"
    noop_baseline.touch()
    args = [
        sys.executable,
        str(driver),
        "--stage",
        "teacher",
        "--model",
        "claude-fable-5-1",
        "--effort",
        "max",
        "--trials",
        "1",
        "--concurrency",
        "1",
        "--run-id",
        run_id,
        "--run-dir",
        str(tmp_path / "runs"),
        "--noop-baseline",
        str(noop_baseline),
    ]
    if resume:
        args.append("--resume")
    command = (
        f"cd {shlex.quote(str(Path.cwd()))}; "
        f"nohup {' '.join(shlex.quote(arg) for arg in args)} "
        f"> {shlex.quote(str(log_path))} 2>&1 < /dev/null & "
        'child=$!; printf "%s\\n" "$child"; wait "$child"'
    )
    env = os.environ.copy()
    project_root = Path(__file__).parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(project_root / "src"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["FAKE_RUN_ROOT"] = str(run_root)
    env["FAKE_AGENT_CALLS"] = str(tmp_path / "agent-create-ids.txt")
    env["FAKE_IMAGE_HANG"] = "0" if resume else "1"
    env["FAKE_IMAGE_EVENTS"] = str(tmp_path / "image-digest-events")
    process = subprocess.Popen(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdout is not None
    return process, int(process.stdout.readline().strip()), log_path


@pytest.mark.parametrize(
    ("sig", "double_signal"), [(signal.SIGINT, False), (signal.SIGTERM, False), (signal.SIGINT, True)]
)
def test_nohup_signal_drain_saves_each_row_once(
    tmp_path: Path,
    sig: signal.Signals,
    double_signal: bool,
) -> None:
    pid, log_path, events = _start_signal_driver(tmp_path, double_signal)
    try:
        assert _read_events(events, 1) == b"R"
        proc_status = Path(f"/proc/{pid}/status").read_text()
        sig_ign = int(next(line.split()[1] for line in proc_status.splitlines() if line.startswith("SigIgn:")), 16)
        assert sig_ign & (1 << (signal.SIGINT - 1))
        with (tmp_path / "install-handler").open("wb") as pipe:
            pipe.write(b"x")
        assert _read_events(events, 2) == b"SS"

        os.kill(pid, sig)
        if double_signal:
            assert _read_events(events, 2) == b"FF"
            os.kill(pid, sig)
        assert _read_events(events, 1) == b"E"
        assert _wait_for_exit(pid)

        assert f"exit_code={128 + sig}" in log_path.read_text()
        rows = [json.loads(line) for line in (tmp_path / "run" / "rows.jsonl").read_text().splitlines()]
        assert Counter(row["task_id"] for row in rows) == Counter(str(index) for index in range(6))
        by_id = {row["task_id"]: row for row in rows}
        for task_id in ("0", "1"):
            expected = "stopped" if double_signal else "valid"
            assert by_id[task_id]["status"] == expected
        for task_id in ("2", "3", "4", "5"):
            assert by_id[task_id]["status"] == "stopped"
            assert by_id[task_id]["stop_reason"] == signal.Signals(sig).name
            assert by_id[task_id]["stop_type"] == "signal_stop"
            assert by_id[task_id]["signal_name"] == signal.Signals(sig).name
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            _wait_for_exit(pid)
        except ProcessLookupError:
            pass
        events.close()


def test_agent_record_survives_setup_signal_and_resume_skips_create(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "image-digest-events")
    process, pid, log_path = _start_agent_resume_driver(tmp_path, resume=False)
    events = (tmp_path / "image-digest-events").open("rb", buffering=0)
    try:
        assert _read_events(events, 1) == b"D"
        os.kill(pid, signal.SIGINT)
        assert process.wait(timeout=5) == 130

        run_root = tmp_path / "runs" / "f1-agent-resume"
        agent_path = run_root / "agent.json"
        assert json.loads(agent_path.read_text()) == {
            "agent_id": "eog-skills-f1-agent-resume",
            "agent_uuid": "fake-agent-uuid",
        }
        assert "exit_code=130" in log_path.read_text()

        resume_process, _, resume_log = _start_agent_resume_driver(tmp_path, resume=True)
        assert resume_process.wait(timeout=30) == 0

        assert "exit_code=0" in resume_log.read_text()
        assert (run_root / "scheduler-reached").read_text() == "yes\n"
        assert (run_root / "manifest.teacher.json").is_file()
        assert (tmp_path / "agent-create-ids.txt").read_text().splitlines() == ["eog-skills-f1-agent-resume"]
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            process.wait(timeout=5)
        events.close()


@pytest.mark.parametrize(
    ("sig", "exit_code"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_nohup_setup_signal_exits_before_row_start(
    tmp_path: Path,
    sig: signal.Signals,
    exit_code: int,
) -> None:
    pid, log_path, events = _start_setup_signal_driver(tmp_path)
    try:
        assert _read_events(events, 1) == b"I"
        assert _read_events(events, 1) == b"S"

        signal_sent_at = time.monotonic()
        os.kill(pid, sig)
        assert _read_events(events, 1, timeout_ms=5000) == b"E"
        assert _wait_for_exit(pid, timeout_ms=5000)
        assert time.monotonic() - signal_sent_at < 5
        assert events.read() == b""

        output = log_path.read_text()
        signal_name = signal.Signals(sig).name
        assert f"Received {signal_name} during setup" in output
        assert f"exit_code={exit_code}" in output
    finally:
        if not _wait_for_exit(pid, timeout_ms=1000):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        events.close()


def test_run_store_manifest_checkpoints_are_thread_safe(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.write_manifest(
        RunManifest(
            git_commit="",
            dataset={},
            image_digests={},
            split_sha256="",
            skills_snapshot_sha256="",
            price_table_sha256="",
        )
    )
    errors: list[Exception] = []
    start = threading.Barrier(8)

    def checkpoint_rows(thread_id: int) -> None:
        try:
            start.wait(timeout=5)
            for row_index in range(50):
                store.checkpoint(
                    {
                        "task_id": f"{thread_id}-{row_index}",
                        "trial": 1,
                        "model": "fake",
                        "effort": "low",
                        "arm": "none",
                        "stage": "fake",
                        "status": "valid",
                        "claude_init_tools": [f"tool-{thread_id}"],
                    }
                )
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=checkpoint_rows, args=(thread_id,)) for thread_id in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    manifest = json.loads(store.manifest_path.read_text())
    assert len(manifest["claude_init_tools_by_row"]) == 400
    assert len(store.rows.path.read_text().splitlines()) == 400
