"""zep-ingest: bulk data ingestion pipeline for Zep.

Everything upstream of the Zep API for getting unstructured and structured
data into Context Graphs correctly: parsing sources, chunking,
contextualization, entity canonicalization, JSON shaping, temporal-correctness
warnings, and rate-limit-aware submission via the Batch API (enterprise) or
sequential graph.add (every plan).

Quickstarts:

    from zep_cloud.client import Zep
    from zep_ingest import ingest_slack_export, ingest_documents, ingest_json_records

    client = Zep(api_key="...")
    ingest_slack_export(client, "export.zip", graph_id="team_knowledge")
    ingest_documents(client, "handbook/**/*.md", graph_id="company_kb")
    ingest_json_records(client, "products.csv", graph_id="catalog", id_field="sku")
"""

from importlib.metadata import PackageNotFoundError, version

from zep_ingest.exceptions import (
    BatchUnavailableError,
    ConfigurationError,
    IngestFailedError,
    IngestTimeoutError,
    ZepDependencyError,
    ZepIngestError,
)
from zep_ingest.loaders.email import EmlLoader
from zep_ingest.loaders.json_records import JsonRecordsLoader
from zep_ingest.loaders.slack import DEFAULT_SKIP_SUBTYPES, SlackExportLoader, SlackMessage
from zep_ingest.loaders.text import TextFileLoader
from zep_ingest.loaders.transcript import TranscriptLoader
from zep_ingest.nodes import NodeItem, ingest_nodes
from zep_ingest.pipeline import (
    Pipeline,
    PreviewReport,
    ingest,
    ingest_documents,
    ingest_emails,
    ingest_json_records,
    ingest_slack_export,
    ingest_transcripts,
)
from zep_ingest.protocols import LLMClient, Loader, Submitter, Transform
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters import BatchSubmitter, SequentialSubmitter, submit_episodes
from zep_ingest.threads import ThreadMessage, ingest_thread_messages
from zep_ingest.transforms.canonicalizer import DEFAULT_RISKY_WORDS, AliasCanonicalizer
from zep_ingest.transforms.chunker import TextChunker
from zep_ingest.transforms.contextualizer import DEFAULT_CONTEXT_PROMPT, LLMContextualizer
from zep_ingest.transforms.json_normalizer import JsonNormalizer
from zep_ingest.transforms.limits import LimitGuard
from zep_ingest.triples import FactTriple, ingest_fact_triples
from zep_ingest.types import (
    MAX_EPISODE_CHARS,
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    MAX_METADATA_KEYS,
    SAFE_EPISODE_CHARS,
    Destination,
    Episode,
)
from zep_ingest.verify import search_when_ready

try:
    __version__ = version("zep-ingest")
except PackageNotFoundError:  # source tree without an editable install
    __version__ = "0.1.0"

__all__ = [
    "DEFAULT_CONTEXT_PROMPT",
    "DEFAULT_RISKY_WORDS",
    "DEFAULT_SKIP_SUBTYPES",
    "MAX_EPISODE_CHARS",
    "MAX_ITEMS_PER_ADD",
    "MAX_ITEMS_PER_BATCH",
    "MAX_METADATA_KEYS",
    "SAFE_EPISODE_CHARS",
    "AddError",
    "AliasCanonicalizer",
    "BatchSubmitter",
    "BatchUnavailableError",
    "ConfigurationError",
    "Destination",
    "EmlLoader",
    "Episode",
    "FactTriple",
    "ThreadMessage",
    "IngestFailedError",
    "IngestResult",
    "IngestTimeoutError",
    "JsonNormalizer",
    "JsonRecordsLoader",
    "LLMClient",
    "LLMContextualizer",
    "LimitGuard",
    "NodeItem",
    "Loader",
    "Pipeline",
    "PreviewReport",
    "SequentialSubmitter",
    "SlackExportLoader",
    "SlackMessage",
    "Submitter",
    "TextChunker",
    "TextFileLoader",
    "TranscriptLoader",
    "Transform",
    "ZepDependencyError",
    "ZepIngestError",
    "__version__",
    "ingest",
    "ingest_documents",
    "ingest_emails",
    "ingest_fact_triples",
    "ingest_json_records",
    "ingest_nodes",
    "ingest_slack_export",
    "ingest_thread_messages",
    "ingest_transcripts",
    "search_when_ready",
    "submit_episodes",
]
