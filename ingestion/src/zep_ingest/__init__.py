"""zep-ingest: bulk data ingestion pipeline for Zep.

Everything upstream of the Zep API for getting unstructured and structured
data into Context Graphs correctly: parsing sources, chunking,
contextualization, entity canonicalization, temporal-correctness
warnings, and rate-limit-aware submission via the Batch API or sequential
graph.add.

Quickstarts:

    from zep_cloud.client import Zep
    from zep_ingest import ingest_slack_export, ingest_documents, ingest_json_records

    client = Zep(api_key="...")

    # Setup is yours, once per graph: zep-ingest writes only into graphs that
    # already exist and already carry their ontology (it is not retroactive).
    # ENTITIES/EDGES are your EntityModel/EdgeModel subclasses, keyed by type
    # name; see the Ontology section of the README for a starter spec.
    for graph_id in ("team_knowledge", "company_kb", "catalog"):
        client.graph.create(graph_id=graph_id)
        client.graph.set_ontology(entities=ENTITIES, edges=EDGES, graph_ids=[graph_id])

    ingest_slack_export(client, "export.zip", graph_id="team_knowledge")
    ingest_documents(client, "handbook/**/*.md", graph_id="company_kb")
    ingest_json_records(client, "products.csv", graph_id="catalog", id_field="sku")
"""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from zep_ingest.exceptions import (
    BatchUnavailableError,
    ConfigurationError,
    IngestFailedError,
    IngestTimeoutError,
    IngestUntrackedError,
    InvalidBatchResponseError,
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
from zep_ingest.submitters import BatchSubmitter, SequentialSubmitter
from zep_ingest.threads import ThreadMessage, ingest_thread_messages
from zep_ingest.transforms.canonicalizer import DEFAULT_RISKY_WORDS, AliasCanonicalizer
from zep_ingest.transforms.chunker import TextChunker
from zep_ingest.transforms.contextualizer import DEFAULT_CONTEXT_PROMPT, LLMContextualizer
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
    __version__ = _version("zep-ingest")
except _PackageNotFoundError:  # source tree without an editable install
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
    "IngestUntrackedError",
    "InvalidBatchResponseError",
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
]
