"""Tests for the exception hierarchy."""

from zep_ingest.exceptions import (
    BatchUnavailableError,
    ConfigurationError,
    IngestFailedError,
    IngestTimeoutError,
    ZepDependencyError,
    ZepIngestError,
)


def test_hierarchy():
    assert issubclass(ConfigurationError, ZepIngestError)
    assert issubclass(BatchUnavailableError, ZepIngestError)
    assert issubclass(IngestTimeoutError, ZepIngestError)
    assert issubclass(IngestFailedError, ZepIngestError)
    assert issubclass(ZepIngestError, Exception)


def test_dependency_error_message():
    err = ZepDependencyError("OpenAI", "pip install zep-ingest[openai]")
    assert isinstance(err, ImportError)
    assert "OpenAI" in str(err)
    assert "pip install zep-ingest[openai]" in str(err)


def test_batch_unavailable_names_the_escape_hatch():
    """The message must say what failed and how to get unstuck. It must not
    assert a plan tier — the Batch API is not gated on one."""
    err = BatchUnavailableError()
    message = str(err)
    assert "Batch API" in message
    assert 'method="sequential"' in message
    assert "plan" not in message.lower()


def test_batch_unavailable_does_not_blame_usage_limits():
    """It is raised for one cause only — a deployment with no batch endpoint —
    so it must not send the reader off to check billing. A refused key or an
    exhausted quota now surfaces as the underlying API error instead."""
    message = str(BatchUnavailableError()).lower()
    assert "not available on this deployment" in message
    for misleading in ("usage limit", "quota", "credit", "dashboard"):
        assert misleading not in message
