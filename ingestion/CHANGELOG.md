# Changelog

All notable changes to `zep-ingest` are documented here.

## 0.1.0 - 2026-07-10

Initial public release.

- Lazy loader/transform pipeline with preview, validation, and warnings before submission.
- Slack, text, email, transcript, and JSON/CSV loaders with source-aware timestamps.
- Text chunking, JSON normalization, alias canonicalization, and optional LLM context.
- Enterprise Batch API submission with sequential fallback, retries, progress, and errors.
- Fact-triple and user-thread ingestion with eager validation and temporal safeguards.
- End-to-end examples and bundled fixtures for common ingestion workflows.
