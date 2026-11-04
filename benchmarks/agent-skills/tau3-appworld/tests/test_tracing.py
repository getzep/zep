import json
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import tracing
from tracing import CURRENT_TRACE, OTLP_PATH, LangfuseTracer


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def _tracer() -> tuple[LangfuseTracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    return LangfuseTracer(exporter=exporter), exporter


def test_tracer_is_disabled_without_credentials(monkeypatch):
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    tracer = LangfuseTracer()
    assert not tracer.enabled
    trace = tracer.start_trace("run", session_id="s", metadata={})
    tracer.end_trace(trace, output={})
    tracer.generation(
        "call", model="m", input="i", output="o", start=datetime.now(UTC), end=datetime.now(UTC)
    )


def test_otlp_exporter_uses_v4_endpoint_and_headers(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com/")
    captured: dict[str, Any] = {}

    def fake_exporter(**kwargs: Any) -> InMemorySpanExporter:
        captured.update(kwargs)
        return InMemorySpanExporter()

    monkeypatch.setattr(tracing, "OTLPSpanExporter", fake_exporter)
    tracer = LangfuseTracer()
    tracer.shutdown()
    assert captured["endpoint"] == "https://us.cloud.langfuse.com" + OTLP_PATH
    assert captured["headers"]["x-langfuse-ingestion-version"] == "4"
    assert captured["headers"]["Authorization"] == "Basic cGs6c2s="


def test_generation_is_child_of_trace_and_carries_trace_attributes():
    tracer, exporter = _tracer()
    trace = tracer.start_trace(
        "tau2-run", session_id="sess-1", metadata={"domain": "airline", "n": 3}
    )
    token = CURRENT_TRACE.set(trace)
    start = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    try:
        tracer.generation(
            "agent.turn",
            model="gpt-5",
            input=[{"role": "user", "content": "hi"}],
            output={"text": "hello"},
            start=start,
            end=start + timedelta(seconds=2),
            usage={"input": 100, "output": 10, "cache_read_input_tokens": 40},
            metadata={"turn": 1},
            level="ERROR",
        )
    finally:
        CURRENT_TRACE.reset(token)
    tracer.end_trace(trace, output={"reward": 1.0})
    tracer.flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    root, generation = spans["tau2-run"], spans["agent.turn"]
    assert root.parent is None and root.context is not None
    assert generation.parent is not None and generation.context is not None
    assert generation.parent.span_id == root.context.span_id
    assert generation.context.trace_id == root.context.trace_id
    assert generation.start_time is not None
    assert generation.start_time == int(start.timestamp() * 1e9)
    assert generation.end_time == generation.start_time + 2_000_000_000

    for span in (root, generation):
        assert _attrs(span)["langfuse.trace.name"] == "tau2-run"
        assert _attrs(span)["langfuse.session.id"] == "sess-1"
        assert _attrs(span)["langfuse.trace.metadata.domain"] == "airline"
        assert _attrs(span)["langfuse.trace.metadata.n"] == "3"

    root_attrs = _attrs(root)
    assert root_attrs["langfuse.observation.type"] == "span"
    assert json.loads(root_attrs["langfuse.observation.output"]) == {"reward": 1.0}
    assert "langfuse.trace.output" not in root_attrs

    attrs = _attrs(generation)
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["langfuse.observation.model.name"] == "gpt-5"
    assert attrs["langfuse.observation.level"] == "ERROR"
    assert attrs["langfuse.observation.metadata.turn"] == "1"
    assert json.loads(attrs["langfuse.observation.output"]) == {"text": "hello"}
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {
        "input": 60,
        "output": 10,
        "cache_read_input_tokens": 40,
    }


def test_generation_without_trace_is_a_root_observation():
    tracer, exporter = _tracer()
    now = datetime.now(UTC)
    tracer.generation("solo", model="m", input="i", output="o", start=now, end=now)
    tracer.flush()
    (span,) = exporter.get_finished_spans()
    assert span.parent is None
    assert _attrs(span)["langfuse.trace.name"] == "solo"
