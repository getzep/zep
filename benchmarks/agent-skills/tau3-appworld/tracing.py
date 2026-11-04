"""Send benchmark LLM calls to Langfuse as OpenTelemetry spans.

Tracing is active only when LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and
LANGFUSE_BASE_URL are set. Spans go to the Langfuse v4 OTLP endpoint
(/api/public/otel/v1/traces with x-langfuse-ingestion-version: 4). Each
observation is exported one time, after it ends, and carries the trace name,
the session ID, and the trace metadata. The benchmarks use synthetic tau2 and
AppWorld data, so a trace keeps the prompt and the output of each call.
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.util.types import AttributeValue

from braintrust_tracing import CURRENT_BRAINTRUST_RECORDER

LOGGER = logging.getLogger("tau2-bench-skills.tracing")
CURRENT_TRACE: ContextVar[dict[str, Any] | None] = ContextVar("CURRENT_TRACE", default=None)
_MAX_TEXT = 200_000
OTLP_PATH = "/api/public/otel/v1/traces"
INGESTION_VERSION = "4"


def response_cache_creation_tokens(usage: Any) -> int:
    def field(source: Any, name: str) -> Any:
        return source.get(name) if isinstance(source, dict) else getattr(source, name, None)

    for source in (
        field(usage, "input_tokens_details"),
        field(usage, "prompt_tokens_details"),
        usage,
    ):
        for name in ("cache_creation_tokens", "cache_write_tokens", "cache_creation_input_tokens"):
            count = int(field(source, name) or 0)
            if count:
                return count
    return 0


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "...[truncated]"
    if isinstance(value, list | tuple):
        return [_bounded(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item) for key, item in value.items() if key != "encrypted_content"
        }
    if value is None or isinstance(value, int | float | bool):
        return value
    if hasattr(value, "model_dump"):
        return _bounded(value.model_dump(mode="json"))
    return _bounded(str(value))


def _json(value: Any) -> str:
    return json.dumps(_bounded(value), default=str)


def _ns(at: datetime) -> int:
    return int(at.timestamp() * 1_000_000_000)


def _metadata_attrs(prefix: str, metadata: dict[str, Any] | None) -> dict[str, AttributeValue]:
    attrs: dict[str, AttributeValue] = {}
    for key, value in (metadata or {}).items():
        attrs[prefix + str(key)] = value if isinstance(value, str) else _json(value)
    return attrs


class LangfuseTracer:
    def __init__(self, exporter: SpanExporter | None = None) -> None:
        self.public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.base_url = os.getenv("LANGFUSE_BASE_URL", os.getenv("LANGFUSE_HOST", "")).rstrip("/")
        self.enabled = exporter is not None or bool(
            self.public_key and self.secret_key and self.base_url
        )
        self._provider: TracerProvider | None = None
        self._tracer: otel_trace.Tracer = otel_trace.NoOpTracer()
        if not self.enabled:
            return
        if exporter is None:
            token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
            exporter = OTLPSpanExporter(
                endpoint=self.base_url + OTLP_PATH,
                headers={
                    "Authorization": f"Basic {token}",
                    "x-langfuse-ingestion-version": INGESTION_VERSION,
                },
                timeout=30,
            )
        # A private provider: the spans do not go to a global OTel provider.
        self._provider = TracerProvider(
            resource=Resource.create({"service.name": "tau2-bench-skills"})
        )
        self._provider.add_span_processor(BatchSpanProcessor(exporter))
        self._tracer = self._provider.get_tracer("tau2-bench-skills.tracing")
        atexit.register(self.shutdown)

    def start_trace(
        self, name: str, *, session_id: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        shared: dict[str, AttributeValue] = {
            "langfuse.trace.name": name,
            "langfuse.session.id": session_id,
            **_metadata_attrs("langfuse.trace.metadata.", metadata),
        }
        span = self._tracer.start_span(
            name, attributes={"langfuse.observation.type": "span", **shared}
        )
        return {"span": span, "shared": shared, "context": otel_trace.set_span_in_context(span)}

    def end_trace(self, trace: dict[str, Any], *, output: Any) -> None:
        span = cast(otel_trace.Span, trace["span"])
        if self.enabled:
            span.set_attribute("langfuse.observation.output", _json(output))
        span.end()

    def generation(
        self,
        name: str,
        *,
        model: str,
        input: Any,
        output: Any,
        start: datetime,
        end: datetime,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        if not self.enabled:
            return
        trace = CURRENT_TRACE.get()
        shared = trace["shared"] if trace else {"langfuse.trace.name": name}
        attrs: dict[str, AttributeValue] = {
            "langfuse.observation.type": "generation",
            **shared,
            "langfuse.observation.model.name": model,
            "langfuse.observation.input": _json(input),
            "langfuse.observation.output": _json(output),
            "langfuse.observation.level": level,
            **_metadata_attrs("langfuse.observation.metadata.", metadata),
        }
        if usage:
            details = dict(usage)
            cache_tokens = details.get("cache_read_input_tokens", 0) + details.get(
                "cache_creation_input_tokens", 0
            )
            details["input"] = max(details.get("input", 0) - cache_tokens, 0)
            attrs["langfuse.observation.usage_details"] = json.dumps(details)
        span = self._tracer.start_span(
            name,
            context=trace["context"] if trace else None,
            attributes=attrs,
            start_time=_ns(start),
        )
        span.end(end_time=_ns(end))

    def flush(self, timeout: float = 60.0) -> None:
        if self._provider is not None and not self._provider.force_flush(int(timeout * 1000)):
            LOGGER.warning("Langfuse flush timed out")

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()
            self._provider = None


TRACER = LangfuseTracer()


def traced_responses_create(client: Any, name: str, kwargs: dict[str, Any]) -> Any:
    start = datetime.now(UTC)
    request = {"instructions": kwargs.get("instructions"), "input": kwargs.get("input")}
    braintrust = CURRENT_BRAINTRUST_RECORDER.get()
    braintrust_request = {"model": kwargs.get("model"), "input": list(kwargs.get("input") or [])}
    try:
        response = client.responses.create(**kwargs)
    except Exception as exc:
        if braintrust is not None:
            braintrust.record_llm(
                name=name,
                request=braintrust_request,
                output=None,
                start=start.timestamp(),
                end=time.time(),
                error=type(exc).__name__,
            )
        TRACER.generation(
            name,
            model=str(kwargs.get("model")),
            input=request,
            output={"error": type(exc).__name__},
            start=start,
            end=datetime.now(UTC),
            level="ERROR",
        )
        raise
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    usage_details = {
        "input": int(getattr(usage, "input_tokens", 0) or 0),
        "output": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(input_details, "cached_tokens", 0) or 0),
        "cache_creation_input_tokens": response_cache_creation_tokens(usage),
    }
    if braintrust is not None:
        braintrust.record_llm(
            name=name,
            request=braintrust_request,
            output=list(getattr(response, "output", None) or []),
            start=start.timestamp(),
            end=time.time(),
            usage=usage_details,
        )
    TRACER.generation(
        name,
        model=str(kwargs.get("model")),
        input=request,
        output=list(getattr(response, "output", None) or []),
        start=start,
        end=datetime.now(UTC),
        usage=usage_details,
        metadata={"reasoning": kwargs.get("reasoning")},
    )
    return response


def instrument_tau2_user() -> None:
    """Trace the tau2 user-simulator completion call in the caller thread."""
    from tau2.utils import llm_utils

    original = llm_utils.completion
    if getattr(original, "_zep_traced", False):
        return

    def completion(*args: Any, **kwargs: Any) -> Any:
        start = datetime.now(UTC)
        response = original(*args, **kwargs)
        try:
            usage = getattr(response, "usage", None)
            TRACER.generation(
                "tau2-user-simulator",
                model=str(kwargs.get("model")),
                input=kwargs.get("messages"),
                output=cast(Any, response).choices[0].message,
                start=start,
                end=datetime.now(UTC),
                usage={
                    "input": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "output": int(getattr(usage, "completion_tokens", 0) or 0),
                },
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("User-simulator trace failed: %s", type(exc).__name__)
        return response

    completion._zep_traced = True  # type: ignore[attr-defined]
    llm_utils.completion = completion
