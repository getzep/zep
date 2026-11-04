import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from supervisor_guard import supervised_workers


@pytest.mark.parametrize("write_fails", [False, True])
def test_supervisor_stops_workers_on_exit(monkeypatch, write_fails):
    kill = Mock()
    monkeypatch.setattr("supervisor_guard.os.killpg", kill)
    worker = SimpleNamespace(pid=123, poll=lambda: None, wait=Mock())
    finished = SimpleNamespace(pid=456, poll=lambda: 0)
    try:
        with supervised_workers() as workers:
            workers.extend([worker, finished])
            if write_fails:
                raise OSError("No space left on device")
    except OSError as exc:
        assert write_fails and str(exc) == "No space left on device"
    else:
        assert not write_fails
    kill.assert_called_once_with(123, signal.SIGTERM)
    worker.wait.assert_called_once_with(timeout=5)


def test_supervisor_kills_an_unresponsive_worker(monkeypatch):
    kill = Mock()
    monkeypatch.setattr("supervisor_guard.os.killpg", kill)
    wait = Mock(side_effect=[subprocess.TimeoutExpired("worker", 5), 0])
    with supervised_workers() as workers:
        workers.append(SimpleNamespace(pid=123, poll=lambda: None, wait=wait))
    assert kill.call_args_list == [((123, signal.SIGTERM),), ((123, signal.SIGKILL),)]


def test_supervisor_handles_no_workers():
    with supervised_workers() as workers:
        assert workers == []
