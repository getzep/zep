# Changelog

All notable changes to `zep-ingest` are documented here.

## Unreleased

- Require `zep-cloud>=3.25.0` and create canonical nodes through the public
  `client.graph.add_nodes` SDK method (`ingest_nodes`), replacing the previous
  private-transport call. The package now uses only the public SDK surface.
- Document `ingest_nodes` / `NodeItem` direct node seeding in the README.
- Fix the ontology guidance in the README: default entity/edge types apply to
  user graphs only, so named (standalone) graphs must declare every type they
  rely on (matching `examples/example_ontology.py`).
- Remove the no-op internal metadata helper (`_capped_metadata`); episode
  metadata is already validated at construction.

## 0.1.0 - 2026-07-10

Initial public release.

- Lazy loader/transform pipeline with preview, validation, and warnings before submission.
- Slack, text, email, transcript, and JSON/CSV loaders with source-aware timestamps.
- Text chunking, JSON normalization, alias canonicalization, and optional LLM context.
- Enterprise Batch API submission with sequential fallback, retries, progress, and errors.
- Fact-triple and user-thread ingestion with eager validation and temporal safeguards.
- End-to-end examples and bundled fixtures for common ingestion workflows.
