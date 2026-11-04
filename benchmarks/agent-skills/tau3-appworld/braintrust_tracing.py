"""Write tau2 episodes to Braintrust project logs as agent traces.

The `braintrust-source` stage uses this module to make the traces that a
Zep trajectory import reads. Each episode is one trace. The rows have the
same shape as the rows that the Braintrust SDK writes:

- The root `task` span is written when the episode starts, with only
  `metrics.start`. The episode end merges the input, the output, the
  `reward` score, and `metrics.end` into the same row.
- Each agent Responses call is one `llm` span. Its input is the Responses
  `input` list and its output is the Responses `output` list.
- Each tool call is one `tool` span, from the agent tool call to the tool
  result.

The user simulator is not traced, because it is not part of the agent.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Self

import httpx

from redaction import Redactor

LOGGER = logging.getLogger("tau2-bench-skills.braintrust")
DEFAULT_API_URL = "https://api.braintrust.dev"
CURRENT_BRAINTRUST_RECORDER: ContextVar[BraintrustRecorder | None] = ContextVar(
    "CURRENT_BRAINTRUST_RECORDER", default=None
)
_MAX_TEXT = 200_000


def _bounded(value: Any, redactor: Redactor | None) -> Any:
    if isinstance(value, str):
        text = redactor.redact(value) if redactor else value
        return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "...[truncated]"
    if isinstance(value, list | tuple):
        return [_bounded(item, redactor) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, redactor)
            for key, item in value.items()
            if key != "encrypted_content"
        }
    if value is None or isinstance(value, int | float | bool):
        return value
    if hasattr(value, "model_dump"):
        return _bounded(value.model_dump(mode="json", exclude_none=True), redactor)
    return _bounded(str(value), redactor)


class BraintrustLogger:
    """Insert project log rows through the Braintrust REST API."""

    def __init__(
        self,
        project_name: str,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key or os.getenv("BRAINTRUST_API_KEY", "")
        if not key:
            raise RuntimeError("BRAINTRUST_API_KEY is required for the braintrust-source stage")
        self._client = httpx.Client(
            base_url=(api_url or os.getenv("BRAINTRUST_API_URL") or DEFAULT_API_URL).rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
            transport=transport,
        )
        self._lock = threading.Lock()
        self.project_name = project_name
        self.project_id = self._project_id(project_name)
        self.rows_sent = 0
        self.rows_failed = 0

    def _project_id(self, name: str) -> str:
        response = self._client.post("/v1/project", json={"name": name})
        response.raise_for_status()
        project_id = response.json().get("id")
        if not project_id:
            raise RuntimeError("Braintrust did not return a project ID")
        return str(project_id)

    def insert(self, rows: list[dict[str, Any]]) -> None:
        for attempt in range(5):
            try:
                response = self._client.post(
                    f"/v1/project_logs/{self.project_id}/insert", json={"events": rows}
                )
            except httpx.HTTPError as exc:
                LOGGER.warning("Braintrust insert failed: %s", type(exc).__name__)
            else:
                if response.status_code < 300:
                    with self._lock:
                        self.rows_sent += len(rows)
                    return
                LOGGER.warning("Braintrust insert status %s", response.status_code)
                if response.status_code < 500 and response.status_code != 429:
                    break
            time.sleep(2**attempt)
        with self._lock:
            self.rows_failed += len(rows)
        raise RuntimeError("Braintrust rejected the trace rows")

    def close(self) -> None:
        self._client.close()


class BraintrustRecorder:
    """A Trajectory recorder that writes the episode to Braintrust, not to Zep."""

    trajectory_uuid: str | None = None

    def __init__(
        self,
        logger: BraintrustLogger,
        *,
        name: str,
        metadata: dict[str, Any],
        tags: list[str],
        redactor: Redactor | None = None,
    ) -> None:
        self.logger = logger
        self.name = name
        self.metadata = metadata
        self.tags = tags
        self.redactor = redactor
        self.root_span_id = str(uuid.uuid4())
        self._first_user_message: str | None = None
        self._last_assistant_message: str | None = None
        self._pending_tools: dict[str, list[dict[str, Any]]] = {}
        self._pending_tools_by_id: dict[str, dict[str, Any]] = {}
        self._succeeded = False
        self._verified = False
        self._reward: float | None = None
        self._token: Any = None

    def _row(self, span_id: str, **fields: Any) -> dict[str, Any]:
        return {
            "id": span_id,
            "span_id": span_id,
            "root_span_id": self.root_span_id,
            **fields,
        }

    def __enter__(self) -> Self:
        self.logger.insert(
            [
                self._row(
                    self.root_span_id,
                    span_parents=[],
                    span_attributes={"name": self.name, "type": "task"},
                    metadata=self.metadata,
                    tags=self.tags,
                    metrics={"start": time.time()},
                )
            ]
        )
        self._token = CURRENT_BRAINTRUST_RECORDER.set(self)
        return self

    def record_message(self, role: str, text: str) -> None:
        if role == "user" and self._first_user_message is None and text.strip():
            self._first_user_message = text
        if role == "assistant":
            self._last_assistant_message = text

    def record_tool_call(self, name: str, arguments: Any, *, call_id: str | None = None) -> None:
        call = {"arguments": arguments, "start": time.time()}
        if call_id is None:
            self._pending_tools.setdefault(name, []).append(call)
        else:
            self._pending_tools_by_id[call_id] = call

    def record_tool_result(self, name: str, result: Any, *, call_id: str | None = None) -> None:
        if call_id is None:
            pending = self._pending_tools.get(name) or []
            call = pending.pop(0) if pending else None
        else:
            call = self._pending_tools_by_id.pop(call_id, None)
        if call is None:
            call = {"arguments": None, "start": time.time()}
        span_id = str(uuid.uuid4())
        self.logger.insert(
            [
                self._row(
                    span_id,
                    span_parents=[self.root_span_id],
                    span_attributes={"name": name, "type": "tool"},
                    input=_bounded(call["arguments"], self.redactor),
                    output=_bounded(result, self.redactor),
                    metrics={"start": call["start"], "end": time.time()},
                )
            ]
        )

    def record_llm(
        self,
        *,
        name: str,
        request: dict[str, Any],
        output: Any,
        start: float,
        end: float,
        usage: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        span_id = str(uuid.uuid4())
        metrics: dict[str, Any] = {"start": start, "end": end}
        if usage:
            metrics.update(
                prompt_tokens=usage.get("input", 0),
                completion_tokens=usage.get("output", 0),
                tokens=usage.get("input", 0) + usage.get("output", 0),
                prompt_cached_tokens=usage.get("cache_read_input_tokens", 0),
                prompt_cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
            )
        row = self._row(
            span_id,
            span_parents=[self.root_span_id],
            span_attributes={"name": name, "type": "llm"},
            input=_bounded(request.get("input"), self.redactor),
            metadata={"model": request.get("model"), "provider": "openai"},
            metrics=metrics,
        )
        if error:
            row["error"] = error
        else:
            row["output"] = _bounded(output, self.redactor)
        self.logger.insert([row])

    def set_result(self, succeeded: bool, verified: bool) -> None:
        self._succeeded = succeeded
        self._verified = verified

    def set_reward(self, reward: float | None) -> None:
        self._reward = reward

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._token is not None:
            CURRENT_BRAINTRUST_RECORDER.reset(self._token)
            self._token = None
        merge: dict[str, Any] = {
            "id": self.root_span_id,
            "_is_merge": True,
            "metrics": {"end": time.time()},
        }
        if self._first_user_message is not None:
            merge["input"] = {
                "messages": [
                    {"role": "user", "content": _bounded(self._first_user_message, self.redactor)}
                ]
            }
        if self._last_assistant_message is not None:
            merge["output"] = _bounded(self._last_assistant_message, self.redactor)
        if exc_type is not None:
            merge["error"] = exc_type.__name__
        elif self._verified:
            reward = self._reward if self._reward is not None else float(self._succeeded)
            merge["scores"] = {"reward": reward}
            merge["metadata"] = {**self.metadata, "succeeded": self._succeeded}
        self.logger.insert([merge])
