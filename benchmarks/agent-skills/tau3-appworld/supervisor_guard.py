"""Stop registered worker groups when their supervisor exits."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, suppress


@contextmanager
def supervised_workers() -> Iterator[list[subprocess.Popen]]:
    workers: list[subprocess.Popen] = []
    try:
        yield workers
    finally:
        running = [worker for worker in workers if worker.poll() is None]
        for worker in running:
            with suppress(ProcessLookupError):
                os.killpg(worker.pid, signal.SIGTERM)
        for worker in running:
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(timeout=5)
