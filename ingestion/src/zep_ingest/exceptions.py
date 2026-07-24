"""Exception classes for zep-ingest.

Philosophy: configuration errors raise immediately, before any API call;
runtime partial failures never raise — they are collected into IngestResult.
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
    """Raised when the Zep Batch API rejects batch creation.

    The Batch API is available on enterprise plans only. Use method="sequential"
    (or the default method="auto", which falls back automatically), or contact
    your Zep account team to enable batch ingestion. See SETUP.md.
    """

    def __init__(self, message: str | None = None, *, partial_result: "IngestResult | None" = None):
        #: IngestResult for batches already submitted before the rejection, if
        #: any — callers must not blindly re-submit everything when this is set.
        self.partial_result = partial_result
        super().__init__(
            message
            or "The Zep Batch API is not available on this plan. It requires an "
            "enterprise plan — contact your Zep account team to enable it, or use "
            'method="sequential" (method="auto" falls back automatically). See SETUP.md.'
        )


class IngestTimeoutError(ZepIngestError):
    """Raised by IngestResult.wait() when processing does not finish in time.

    The IngestResult remains usable; call wait() again or inspect progress.
    """


class IngestFailedError(ZepIngestError):
    """Raised only by the opt-in IngestResult.raise_for_status() when items failed."""


class ZepDependencyError(ImportError):
    """Raised when an optional dependency (e.g. an LLM SDK) is not installed."""

    def __init__(self, framework: str, install_command: str):
        self.framework = framework
        self.install_command = install_command
        super().__init__(f"{framework} dependencies not found. Install with: {install_command}")
