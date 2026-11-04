"""A fail-closed filesystem boundary for the upstream banking shell."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

_SANDBOX_CLEANUP: ContextVar[ExitStack | None] = ContextVar("sandbox_cleanup", default=None)
_INSTALL_LOCK = threading.Lock()


def _cleanup_sandbox(sandbox: Any) -> None:
    if getattr(sandbox, "sandbox_dir", None) is not None:
        sandbox.cleanup()


@contextmanager
def sandbox_lifecycle() -> Iterator[None]:
    install_sandbox_guard()
    with ExitStack() as cleanup:
        token = _SANDBOX_CLEANUP.set(cleanup)
        try:
            yield
        finally:
            _SANDBOX_CLEANUP.reset(token)


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def isolated_command(sandbox: Any, command: str, timeout: int = 30) -> tuple[int, str, str]:
    if sandbox.allow_writes:
        raise ValueError("Writable banking sandboxes are forbidden")
    binary = shutil.which("bwrap")
    if not binary:
        raise RuntimeError("Install bubblewrap before running the banking benchmark")
    args = [binary, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).is_symlink():
            args += ["--symlink", os.readlink(path), path]
        elif Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for binary_name in ("env", "printenv"):
        path = shutil.which(binary_name)
        if path:
            args += ["--ro-bind", "/dev/null", str(Path(path).resolve())]
    args += [
        "--ro-bind",
        str(sandbox.kb_dir),
        "/kb",
        "--chdir",
        "/kb",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/nonexistent",
        "/bin/sh",
        "-c",
        command,
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=False, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, "", "The sandbox command timed out"
    return result.returncode, _decode_output(result.stdout), _decode_output(result.stderr)


def install_sandbox_guard() -> None:
    from tau2.knowledge.sandbox_manager import SandboxManager

    with _INSTALL_LOCK:
        SandboxManager.run_command = isolated_command
        if getattr(SandboxManager.__init__, "_sandbox_guard", False):
            return
        original_init = SandboxManager.__init__

        @wraps(original_init)
        def initialize(sandbox: Any, *args: Any, **kwargs: Any) -> None:
            cleanup = _SANDBOX_CLEANUP.get()
            if cleanup is not None:
                cleanup.callback(_cleanup_sandbox, sandbox)
            original_init(sandbox, *args, **kwargs)

        initialize._sandbox_guard = True
        SandboxManager.__init__ = initialize


def probe_commands() -> list[tuple[str, str]]:
    root = Path(__file__).resolve().parent
    data = Path(os.environ.get("TAU2_DATA_DIR", root.parents[2] / "tau2-bench/data"))
    from tau2 import evaluator

    paths = {
        "tasks_absolute": data / "tau2/domains/banking_knowledge/tasks.json",
        "evaluator": Path(evaluator.__file__).with_name("evaluator.py"),
        "results": root / "results",
        "rerun": Path.home() / "tau3-rerun",
        "memory": Path.home() / "memory",
    }
    commands = [
        (
            label,
            f"{'cat' if label in {'tasks_absolute', 'evaluator'} else 'ls -ld'} {shlex.quote(str(path))}",
        )
        for label, path in paths.items()
    ]
    commands += [
        ("tasks_traversal", f"cat ../../{str(paths['tasks_absolute']).lstrip('/')}"),
        ("environment", "env"),
        ("network", "curl --connect-timeout 2 --max-time 3 -fsS https://example.com"),
        ("write", "touch /kb/forbidden-write"),
    ]
    return commands


def check_sandbox(sandbox: Any) -> list[dict[str, Any]]:
    checks = []
    code, out, err = sandbox.run_command("find /kb -type f | head -n 1 | xargs cat")
    if code or not out.strip():
        raise RuntimeError(f"The sandbox cannot read KB documents: {err[:200]}")
    checks.append(
        {"check": "kb_read", "command": "find /kb -type f | head -n 1 | xargs cat", "code": code}
    )
    for name, command in probe_commands():
        code, out, err = sandbox.run_command(command, timeout=5)
        checks.append({"check": name, "command": command, "code": code, "error": err.strip()[:300]})
        if code == 0:
            raise RuntimeError(f"Sandbox isolation failed: {name}")
    return checks


def sandbox_self_check() -> list[dict[str, Any]]:
    from tau2.domains.banking_knowledge.environment import get_knowledge_base
    from tau2.domains.banking_knowledge.retrieval import RETRIEVAL_VARIANTS, _create_sandbox

    install_sandbox_guard()
    sandbox = _create_sandbox(get_knowledge_base(), RETRIEVAL_VARIANTS["alltools"].shell)
    try:
        return check_sandbox(sandbox)
    finally:
        sandbox.cleanup()
