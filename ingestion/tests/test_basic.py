"""Package-level smoke tests: imports and __all__."""

import zep_ingest


def test_public_api_exports():
    """__all__ is pinned exactly, not as a subset: widening the public surface
    is as much a deliberate, reviewable decision as narrowing it, and everything
    listed here is supported from 0.1.0 onward."""
    expected = {
        # core
        "Episode",
        "Destination",
        "IngestResult",
        "AddError",
        "PreviewReport",
        # limits
        "MAX_EPISODE_CHARS",
        "SAFE_EPISODE_CHARS",
        "MAX_ITEMS_PER_ADD",
        "MAX_ITEMS_PER_BATCH",
        "DEFAULT_ITEMS_PER_BATCH",
        "MAX_METADATA_KEYS",
        "MIN_WAIT_TIMEOUT_SECONDS",
        "SECONDS_PER_SUBMITTED_ITEM",
        # protocols
        "Loader",
        "Transform",
        "Submitter",
        "LLMClient",
        # pipeline
        "Pipeline",
        "ingest",
        "ingest_slack_export",
        "ingest_documents",
        "ingest_emails",
        "ingest_json_records",
        "ingest_transcripts",
        "ingest_fact_triples",
        "ingest_thread_messages",
        "ingest_nodes",
        # loaders
        "SlackExportLoader",
        "SlackMessage",
        "DEFAULT_SKIP_SUBTYPES",
        "TextFileLoader",
        "JsonRecordsLoader",
        "EmlLoader",
        "TranscriptLoader",
        "ConcatLoader",
        "SourcePaths",
        # transforms
        "TextChunker",
        "LLMContextualizer",
        "DEFAULT_CONTEXT_PROMPT",
        "AliasCanonicalizer",
        "DEFAULT_RISKY_WORDS",
        "LimitGuard",
        # submitters
        "BatchSubmitter",
        "SequentialSubmitter",
        # triples, threads & nodes
        "FactTriple",
        "ThreadMessage",
        "NodeItem",
        # verification
        "search_when_ready",
        "wait_timeout_seconds",
        # exceptions
        "ZepIngestError",
        "ConfigurationError",
        "BatchUnavailableError",
        "IngestTimeoutError",
        "IngestFailedError",
        "IngestUntrackedError",
        "InvalidBatchResponseError",
        "ZepDependencyError",
        # metadata
        "__version__",
    }
    actual = set(zep_ingest.__all__)
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    assert not added, f"__all__ gained unpinned public names: {added}"
    assert not removed, f"__all__ lost pinned public names: {removed}"
    for name in zep_ingest.__all__:
        assert getattr(zep_ingest, name) is not None


def test_submit_episodes_demoted_from_top_level():
    """submit_episodes is intentionally not part of the top-level public API:
    it skips the LimitGuard/validation that Pipeline.run applies, and its
    batch-tuning parameters are still being optimized. It stays reachable via
    the submitters submodule for advanced callers with pre-built episodes."""
    assert "submit_episodes" not in zep_ingest.__all__
    assert not hasattr(zep_ingest, "submit_episodes")

    from zep_ingest.submitters import submit_episodes

    assert callable(submit_episodes)
