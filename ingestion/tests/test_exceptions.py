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


def test_batch_unavailable_mentions_plan():
    err = BatchUnavailableError()
    message = str(err)
    assert "Batch API" in message
    assert "plan" in message.lower()
