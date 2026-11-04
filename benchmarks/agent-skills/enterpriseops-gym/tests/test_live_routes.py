from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from eog.providers.anthropic_route import AnthropicRoute
from eog.providers.openai_route import OpenAIRoute

LIVE = os.getenv("EOG_RUN_LIVE") == "1"
pytestmark = [pytest.mark.live, pytest.mark.skipif(not LIVE, reason="Set EOG_RUN_LIVE=1 to run paid route checks.")]


def _error_details(error: BaseException) -> tuple[int | None, str]:
    current: BaseException | None = error
    while current is not None:
        response = getattr(current, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            try:
                body = json.dumps(response.json(), ensure_ascii=False)
            except (ValueError, AttributeError):
                body = str(getattr(response, "text", ""))
            body = re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer [REDACTED]", body)
            body = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", body)
            return status, body
        current = current.__cause__
    return None, type(error).__name__


def _run_check(route: Any, *, model: str, effort: str, tmp_path: Path) -> None:
    turn = None
    error = None
    try:
        turn = route.complete(
            "Reply with OK.",
            [{"role": "user", "content": "OK"}],
            [],
            effort=effort,
            task_id="live-route-check",
            attempt_limit=1,
        )
    except Exception as exc:
        error = exc
    status, body = _error_details(error) if error else (200, "")
    usage = asdict(turn.usage) if turn else {}
    raw_usage = turn.raw_usage if turn else {}
    output = Path(__file__).resolve().parents[1] / "results" / "preflight"
    output.mkdir(parents=True, exist_ok=True)
    request_path = next(iter(tmp_path.rglob("*.json")), None)
    request = json.loads(request_path.read_text()) if request_path else {}
    (output / f"{model}.request.json").write_text(json.dumps(request, indent=2) + "\n")
    (output / f"{model}.result.json").write_text(
        json.dumps(
            {
                "model": model,
                "effort": effort,
                "http_status": status,
                "error_body": body,
                "usage": usage,
                "raw_usage_fields": sorted(raw_usage),
                "temperature": request.get("temperature"),
                "temperature_result": "omitted" if "temperature" not in request else "sent",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    assert "temperature" not in request


def test_anthropic_opus_request_and_usage(tmp_path: Path) -> None:
    _run_check(
        AnthropicRoute("claude-opus-5-5", capture_dir=tmp_path / "opus"),
        model="claude-opus-5-5",
        effort="max",
        tmp_path=tmp_path,
    )


def test_anthropic_haiku_request_and_usage(tmp_path: Path) -> None:
    _run_check(
        AnthropicRoute("claude-haiku-5-5", capture_dir=tmp_path / "haiku"),
        model="claude-haiku-5-5",
        effort="low",
        tmp_path=tmp_path,
    )


def test_openai_luna_request_and_usage(tmp_path: Path) -> None:
    _run_check(
        OpenAIRoute(capture_dir=tmp_path / "luna"),
        model="gpt-6-luna",
        effort="low",
        tmp_path=tmp_path,
    )
