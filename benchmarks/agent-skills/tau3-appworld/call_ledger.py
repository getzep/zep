"""Count each failed HTTP attempt of an episode by service, operation, and outcome.

Client retries are silent, so the ledger counts every attempt at the transport level.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from contextvars import ContextVar
from typing import Any

import httpx

from redaction import Redactor

ROUTE_WORDS = frozenset(
    {
        # Zep v4 routes
        *[
            "abandon",
            "agents",
            "api",
            "approve",
            "breaking-changes",
            "candidates",
            "close",
            "compare",
            "context",
        ],
        *[
            "correct-task-family",
            "evaluations",
            "events",
            "evidence",
            "export",
            "import",
            "invalidate-evidence",
        ],
        *[
            "items",
            "learning",
            "learning-runs",
            "list",
            "literal-policy",
            "outcome",
            "pause",
            "publication",
        ],
        *[
            "relations",
            "rename",
            "reopen",
            "restore-version",
            "resume",
            "retire",
            "revoke",
            "runs",
            "search",
        ],
        *[
            "skill-candidates",
            "skill-publications",
            "skills",
            "split-plan",
            "summary",
            "task-families",
        ],
        *["trace-imports", "traces", "uses", "v4", "verifiers", "versions"],
        # Model provider routes
        *["v1", "responses", "chat", "completions", "messages"],
        # AppWorld server routes
        *["initialize", "execute", "evaluate", "task_completed"],
    }
)
ZEP_TIMEOUT_S = 60.0
MAX_MESSAGE_CHARS = 200


INFRASTRUCTURE_ERRORS = frozenset(
    {
        # Transport errors and timeouts (httpx, the OpenAI and Anthropic SDKs, and Python)
        *[
            "HTTPError",
            "TransportError",
            "NetworkError",
            "ConnectError",
            "ReadError",
            "WriteError",
            "CloseError",
        ],
        *[
            "RemoteProtocolError",
            "LocalProtocolError",
            "ProxyError",
            "TimeoutException",
            "ConnectTimeout",
        ],
        *["ReadTimeout", "WriteTimeout", "PoolTimeout", "APIConnectionError", "APITimeoutError"],
        *["TimeoutError", "ConnectionError"],
        # Provider status errors for 5xx and 429
        *["InternalServerError", "RateLimitError", "OverloadedError", "ServiceUnavailableError"],
        # Zep SDK errors
        "ApiError",
    }
)
_DETAIL_STATUS = re.compile(r"^\w+:(\d{3})\b")


def is_infrastructure_error(
    error: str | None, detail: str | None = None, error_type: str | None = None
) -> bool:
    """Return True for an episode error that `--resume` runs again.

    Transport errors, timeouts, 5xx, 429, and Zep errors are infrastructure faults. An agent
    outcome (a context limit, an empty turn, a provider 400, a failed response) is final.
    """
    if error_type == "agent_broke_environment":
        return False
    if error_type in {"appworld_server_error", "server_state_changed"}:
        return True
    if not error:
        return False
    if error in INFRASTRUCTURE_ERRORS:
        return True
    match = _DETAIL_STATUS.match(detail or "")
    if match is None:
        return False
    status = int(match.group(1))
    # An Anthropic stream error event arrives after an HTTP 200 response.
    return status >= 500 or status == 429 or (error == "APIStatusError" and status < 400)


class CallLedger:
    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str, str]] = Counter()
        self._attempts: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, service: str, operation: str, outcome: str) -> None:
        with self._lock:
            self._counts[(service, operation, outcome)] += 1

    def record_attempt(self, attempt: dict[str, Any]) -> int:
        with self._lock:
            self._attempts.append(attempt)
            return len(self._attempts) - 1

    def update_attempt(self, attempt_id: int, **values: Any) -> None:
        with self._lock:
            self._attempts[attempt_id].update(values)

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._counts.items())
        return [
            {"service": service, "operation": operation, "outcome": outcome, "count": count}
            for (service, operation, outcome), count in items
        ]

    def attempts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(attempt) for attempt in self._attempts]


CURRENT_LEDGER: ContextVar[CallLedger | None] = ContextVar("CURRENT_LEDGER", default=None)


def record_failure(service: str, operation: str, outcome: str) -> None:
    ledger = CURRENT_LEDGER.get()
    if ledger is not None:
        ledger.record(service, operation, outcome)


def operation_name(request: httpx.Request) -> str:
    """Keep only fixed route words, so that no ID, name, or task value is stored."""
    segments = [
        segment if segment in ROUTE_WORDS else "{id}"
        for segment in request.url.path.split("/")
        if segment
    ]
    return f"{request.method} /{'/'.join(segments)}"


def error_detail(exc: BaseException, redactor: Redactor | None = None) -> str:
    """Return the class, the status, and a short redacted message.

    The text of a status error holds the response headers or body, so it is not stored.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    prefix = f"{type(exc).__name__}:{status}" if status is not None else type(exc).__name__
    if status is not None:
        return prefix
    lines = str(exc).splitlines()
    message = (redactor or Redactor()).redact(lines[0] if lines else "")[:MAX_MESSAGE_CHARS]
    return f"{prefix}: {message}" if message else prefix


class _LedgerStream(httpx.SyncByteStream):
    def __init__(
        self,
        inner: httpx.SyncByteStream,
        ledger: CallLedger | None,
        service: str,
        operation: str,
        attempt_id: int | None,
        started: float,
    ) -> None:
        self.inner = inner
        self.ledger = ledger
        self.service = service
        self.operation = operation
        self.attempt_id = attempt_id
        self.started = started

    def __iter__(self):
        try:
            yield from self.inner
        except httpx.HTTPError as exc:
            if self.ledger is not None:
                self.ledger.record(self.service, self.operation, type(exc).__name__)
                if self.attempt_id is not None:
                    self.ledger.update_attempt(
                        self.attempt_id,
                        error=type(exc).__name__,
                        failed=True,
                    )
            raise
        finally:
            if self.ledger is not None and self.attempt_id is not None:
                self.ledger.update_attempt(
                    self.attempt_id,
                    latency_s=time.monotonic() - self.started,
                )

    def close(self) -> None:
        try:
            self.inner.close()
        finally:
            if self.ledger is not None and self.attempt_id is not None:
                self.ledger.update_attempt(
                    self.attempt_id,
                    latency_s=time.monotonic() - self.started,
                )


def _model_request_fields(request: httpx.Request, service: str) -> dict[str, Any]:
    if service not in {"model", "openai", "anthropic"}:
        return {}
    try:
        body = json.loads(request.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"model": None, "effort": None}
    reasoning = body.get("reasoning") or {}
    output_config = body.get("output_config") or {}
    return {
        "model": body.get("model"),
        "effort": reasoning.get("effort") or output_config.get("effort"),
    }


class LedgerTransport(httpx.BaseTransport):
    def __init__(self, service: str, inner: httpx.BaseTransport | None = None) -> None:
        self.service = service
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        ledger = CURRENT_LEDGER.get()
        operation = operation_name(request)
        started = time.monotonic()
        try:
            response = self.inner.handle_request(request)
        except httpx.HTTPError as exc:
            if ledger is not None:
                ledger.record(self.service, operation, type(exc).__name__)
                ledger.record_attempt(
                    {
                        "service": self.service,
                        "operation": operation,
                        "status": None,
                        "latency_s": time.monotonic() - started,
                        "error": type(exc).__name__,
                        "failed": True,
                        **_model_request_fields(request, self.service),
                    }
                )
            raise
        attempt_id = None
        if ledger is not None:
            status = response.status_code
            if status >= 400:
                ledger.record(self.service, operation, str(status))
            attempt_id = ledger.record_attempt(
                {
                    "service": self.service,
                    "operation": operation,
                    "status": status,
                    "latency_s": time.monotonic() - started,
                    "error": None,
                    "failed": status >= 400,
                    **_model_request_fields(request, self.service),
                }
            )
        if isinstance(response.stream, httpx.SyncByteStream):
            response.stream = _LedgerStream(
                response.stream,
                ledger,
                self.service,
                operation,
                attempt_id,
                started,
            )
        return response

    def close(self) -> None:
        self.inner.close()


def ledger_client(service: str, **kwargs: Any) -> httpx.Client:
    return httpx.Client(transport=LedgerTransport(service), **kwargs)


def zep_http_client() -> httpx.Client:
    """The Zep SDK uses 60 s only when it makes its own client, so keep that timeout here."""
    return ledger_client("zep", timeout=ZEP_TIMEOUT_S, follow_redirects=True)


def openai_http_client(service: str) -> httpx.Client:
    import openai

    return openai.DefaultHttpxClient(transport=LedgerTransport(service))


def anthropic_http_client(service: str) -> httpx.Client:
    import anthropic

    return anthropic.DefaultHttpxClient(transport=LedgerTransport(service))
