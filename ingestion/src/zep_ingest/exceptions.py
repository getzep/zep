"""Exception classes for zep-ingest.

Philosophy: configuration errors and unusable API responses raise immediately;
per-item runtime failures are collected into IngestResult. Waiting also raises
when completion cannot be tracked safely.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zep_ingest.result import IngestResult


class ZepIngestError(Exception):
    """Base class for all zep-ingest errors."""


class ConfigurationError(ZepIngestError):
    """Raised eagerly for invalid configuration (destination, paths, alias maps,
    fact triples) before any API call is made."""


class BatchUnavailableError(ZepIngestError):
    """Raised when the Zep deployment being used does not serve the Batch API.

    Batch is the default submission path, but not every deployment exposes it.
    This is the one failure sequential ingestion can work around, so it is the
    only one raised here: a refused key or an exhausted quota surfaces as the
    underlying API error instead, since sequential graph.add would be refused
    the same way. Use method="sequential" (or the default method="auto", which
    falls back automatically). See SETUP.md.
    """

    def __init__(self, message: str | None = None, *, partial_result: "IngestResult | None" = None):
        #: IngestResult for batches already submitted before the failure, if
        #: any — callers must not blindly re-submit everything when this is set.
        self.partial_result = partial_result
        super().__init__(
            message
            or "The Zep Batch API is not available on this deployment: the batch "
            "endpoint was not found. Check that the client is pointed at a Zep "
            'deployment that supports batching, or use method="sequential" '
            '(method="auto" falls back automatically). See SETUP.md.'
        )


class InvalidBatchResponseError(ZepIngestError):
    """Raised when batch creation returns no usable batch ID — because the
    response omitted it, or because a transport error meant no response arrived
    and the batch may exist unidentifiable."""

    def __init__(
        self,
        message: str,
        *,
        partial_result: "IngestResult | None" = None,
    ) -> None:
        self.partial_result = partial_result
        super().__init__(message)


class IngestTimeoutError(ZepIngestError):
    """Raised by IngestResult.wait() when processing does not finish in time.

    The IngestResult remains usable; call wait() again or inspect progress.
    """


class IngestFailedError(ZepIngestError):
    """Raised only by the opt-in IngestResult.raise_for_status() when items failed."""


class IngestUntrackedError(ZepIngestError):
    """Raised when wait() cannot observe completion because the API returned no handle."""


class ZepDependencyError(ImportError):
    """Raised when an optional dependency (e.g. an LLM SDK) is not installed."""

    def __init__(self, framework: str, install_command: str):
        self.framework = framework
        self.install_command = install_command
        super().__init__(f"{framework} dependencies not found. Install with: {install_command}")
