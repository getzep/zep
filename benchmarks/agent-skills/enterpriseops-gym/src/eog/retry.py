from __future__ import annotations

import email.utils
import json
import random
import re
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC
from typing import Any

import httpx

from .errors import (
    ContextOverflowError,
    NonRetryableProviderError,
    PolicyFlagError,
    RetryExhaustedError,
    WallClockLimitError,
)
from .providers.base import check_abort_requested

MAX_ATTEMPTS = 30
BASE_DELAY = 2.0
MAX_DELAY = 120.0
RETRYABLE_ZAI_CODES = {1302, 1305}
NONRETRYABLE_ZAI_CODES = {1113, *range(1308, 1322)}
QUOTA_CODES = {1113}
SECRET_PATTERN = re.compile(
    r"(?i)(\b(?:api[_ -]?key|authorization|token|secret|password)\b\s*[:=]\s*)[\"']?[^,\s\"'}]+"
)
TASK_DATA_PATTERN = re.compile(
    r"(?i)(\b(?:prompt|messages|content|input|task[_ -]?(?:payload|data))\b\s*[:=]\s*)"
    r"(?:\[[^\]]*\]|\{[^}]*\}|[^,;\n]+)"
)
TOKEN_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+|\b(?:sk-[a-z0-9_-]{8,}|zep_[a-z0-9_-]{8,})\b")


def _response(error: BaseException) -> tuple[int | None, dict[str, Any], dict[str, str]]:
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None)
    if response is not None:
        status = getattr(response, "status_code", status)
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = {}
        return status, body if isinstance(body, dict) else {}, dict(response.headers)
    body = getattr(error, "body", {})
    if not isinstance(body, dict):
        dump = getattr(body, "model_dump", None)
        if callable(dump):
            body = dump()
    return status, body if isinstance(body, dict) else {}, {}


def _provider_code(body: dict[str, Any]) -> int | str | None:
    error = body.get("error", body)
    if not isinstance(error, dict):
        return None
    code = error.get("code", error.get("error_code"))
    try:
        return int(code)
    except (TypeError, ValueError):
        return code


def _safe_error_message(message: Any, *, max_chars: int = 300) -> str:
    if not isinstance(message, str):
        return "[error message unavailable]"
    safe = TOKEN_PATTERN.sub("[redacted]", message)
    safe = SECRET_PATTERN.sub(r"\1[redacted]", safe)
    safe = TASK_DATA_PATTERN.sub(r"\1[redacted]", safe)
    return " ".join(safe.split())[:max_chars] or "[error message unavailable]"


def safe_diagnostic_text(message: Any, *, max_chars: int = 4000) -> str:
    if not isinstance(message, str) or not message:
        return ""
    safe = TOKEN_PATTERN.sub("[redacted]", message)
    safe = SECRET_PATTERN.sub(r"\1[redacted]", safe)
    safe = TASK_DATA_PATTERN.sub(r"\1[redacted]", safe)
    return " ".join(safe.split())[-max_chars:]


def failure_details(error: BaseException, *, cli_stderr_tail: str | None = None) -> dict[str, Any]:
    current = error
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        cause = current.__cause__ or current.__context__
        if cause is None:
            break
        current = cause

    status, body, _ = _response(current)
    code = _provider_code(body)
    if code is None:
        code = getattr(current, "code", None)
    error_body = body.get("error", body)
    message = None
    if isinstance(error_body, dict):
        message = error_body.get("message", error_body.get("detail"))
    if message is None:
        message = getattr(current, "message", None)
    if message is None:
        message = str(current) or type(current).__name__
    details = {
        "http_status": status,
        "provider_code": code,
        "error_message": _safe_error_message(message),
        "underlying_exception_class": type(current).__name__,
        "underlying_exception_message": _safe_error_message(str(current)),
    }
    if cli_stderr_tail is not None:
        details["cli_stderr_tail"] = safe_diagnostic_text(cli_stderr_tail)
    return details


def _retry_after(headers: dict[str, str], now: float) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, parsed.timestamp() - now)
        except (TypeError, ValueError, OverflowError):
            return None


def is_context_overflow(status: int | None, code: int | str | None, text: str) -> bool:
    return status == 400 and (
        code == "context_length_exceeded"
        or code == 1261
        or "prompt is too long" in text
        or "maximum context length is" in text
    )


def classify_error(error: BaseException) -> tuple[bool, str, int | None, int | str | None]:
    status, body, _ = _response(error)
    code = _provider_code(body)
    text = json.dumps(body).lower()
    exception_name = type(error).__name__.lower()
    if code == "invalid_prompt" or "invalid_prompt" in text:
        return False, "invalid_prompt", status, code
    if code in QUOTA_CODES or code == "insufficient_quota" or "insufficient_quota" in text:
        return False, "provider_quota_or_balance", status, code
    if code in RETRYABLE_ZAI_CODES:
        return True, "zai_business_error", status, code
    if code in NONRETRYABLE_ZAI_CODES:
        return False, "provider_nonretryable_code", status, code
    if is_context_overflow(status, code, text):
        return False, "context_overflow", status, code
    if status in (400, 401, 403, 404):
        return False, "http_nonretryable", status, code
    if status in (408, 429) or (status is not None and status >= 500):
        return True, "http_retryable", status, code
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return True, "timeout", status, code
    if isinstance(error, (httpx.TransportError, ConnectionError, OSError)):
        return True, "transport", status, code
    if "timeout" in exception_name or "connection" in exception_name:
        return True, "transport", status, code
    return False, "provider_error", status, code


def _attempt_failure_context(context: dict[str, Any] | None) -> dict[str, Any]:
    excluded = {
        "abort_requested",
        "attempt_failures",
        "model_call_http_statuses",
        "model_call_latencies",
        "deadline_monotonic",
    }
    return {
        key: deepcopy(value) if isinstance(value, (dict, list)) else value
        for key, value in (context or {}).items()
        if key not in excluded
    }


def _record_http_status(context: dict[str, Any] | None, status: Any) -> None:
    statuses = context.get("model_call_http_statuses") if context is not None else None
    if isinstance(statuses, list):
        statuses.append(status if isinstance(status, int) else None)


def call_with_retry(
    fn: Callable[..., Any],
    *,
    route: str,
    ledger: Any,
    context: dict[str, Any] | None = None,
    request_fields: dict[str, Any] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay: float = BASE_DELAY,
    max_delay: float = MAX_DELAY,
    sleep: Callable[[float], None] = time.sleep,
    uniform: Callable[[float, float], float] = random.uniform,
    clock: Callable[[], float] = time.time,
    on_success_duration: Callable[[float], None] | None = None,
    deadline_monotonic: float | None = None,
    model_timeout: float | None = None,
) -> Any:
    for attempt in range(1, max_attempts + 1):
        check_abort_requested(context)
        if ledger is not None and callable(getattr(ledger, "check_budget", None)):
            ledger.check_budget()
        started = time.monotonic()
        remaining = deadline_monotonic - started if deadline_monotonic is not None else None
        if remaining is not None and remaining < 1.0:
            entry = _attempt_failure_context(context)
            entry.update(request_fields or {})
            entry.update(
                route=route,
                attempt=attempt,
                error_class="unbilled_timeout",
                reason="wall_clock_limit",
                http_status=None,
                provider_code=None,
                error_message="Episode wall-clock limit reached.",
                delay_seconds=0.0,
                latency_seconds=0.0,
                billing_status="unbilled_timeout",
                cost_usd=None,
            )
            if context is not None and isinstance(context.get("attempt_failures"), list):
                context["attempt_failures"].append(entry.copy())
            if ledger is not None:
                ledger.append_failure(entry)
            raise WallClockLimitError(f"Episode wall-clock limit reached for {route}")
        try:
            if model_timeout is not None:
                timeout = model_timeout if remaining is None else min(model_timeout, remaining)
                result = fn(timeout)
            else:
                result = fn()
            _record_http_status(context, getattr(result, "status_code", None))
            if on_success_duration is not None:
                on_success_duration(time.monotonic() - started)
            return result
        except PolicyFlagError:
            raise
        except Exception as error:
            call_latency = time.monotonic() - started
            retryable, error_class, status, code = classify_error(error)
            _record_http_status(context, status)
            delay = 0.0
            _, _, headers = _response(error)
            if retryable and attempt < max_attempts:
                requested = _retry_after(headers, clock())
                delay = (
                    requested if requested is not None else uniform(0, min(max_delay, base_delay * 2 ** (attempt - 1)))
                )
            attempt_context = _attempt_failure_context(context)
            entry = {
                **attempt_context,
                **(request_fields or {}),
                "route": route,
                "attempt": attempt,
                "error_class": error_class,
                "http_status": status,
                "provider_code": code,
                "error_message": failure_details(error)["error_message"],
                "delay_seconds": delay,
                "latency_seconds": call_latency,
            }
            if error_class == "timeout":
                entry.update(billing_status="unbilled_timeout", cost_usd=None)
            if context is not None:
                failures = context.get("attempt_failures")
                if isinstance(failures, list):
                    failures.append(entry.copy())
                latencies = context.get("model_call_latencies")
                if isinstance(latencies, list):
                    latencies.append(call_latency)
            if ledger is not None:
                ledger.append_failure(entry)
            if error_class == "invalid_prompt":
                raise PolicyFlagError("Provider rejected the prompt as invalid_prompt") from error
            if error_class == "context_overflow":
                raise ContextOverflowError(f"Prompt exceeded the context length for {route}") from error
            if not retryable:
                raise NonRetryableProviderError(f"Non-retryable provider error for {route}") from error
            if attempt == max_attempts:
                raise RetryExhaustedError(f"Provider retry limit reached for {route}") from error
            if deadline_monotonic is not None and time.monotonic() + delay >= deadline_monotonic:
                delay = max(0.0, deadline_monotonic - time.monotonic())
            sleep(delay)
    raise AssertionError("unreachable")
