"""Package-level smoke tests: imports, __all__, version."""

import zep_ingest


def test_version():
    assert zep_ingest.__version__ == "0.1.0"


def test_public_api_exports():
    expected = {
        # core
        "Episode",
        "Destination",
        "IngestResult",
        "AddError",
        "PreviewReport",
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
        # loaders
        "SlackExportLoader",
        "TextFileLoader",
        "JsonRecordsLoader",
        "EmlLoader",
        "TranscriptLoader",
        # transforms
        "TextChunker",
        "LLMContextualizer",
        "DEFAULT_CONTEXT_PROMPT",
        "AliasCanonicalizer",
        "LimitGuard",
        # submitters
        "BatchSubmitter",
        "SequentialSubmitter",
        # triples & threads
        "FactTriple",
        "ThreadMessage",
        # exceptions
        "ZepIngestError",
        "ConfigurationError",
        "BatchUnavailableError",
        "IngestTimeoutError",
        "IngestFailedError",
        "IngestUntrackedError",
        "InvalidBatchResponseError",
        "ZepDependencyError",
    }
    assert expected <= set(zep_ingest.__all__)
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
