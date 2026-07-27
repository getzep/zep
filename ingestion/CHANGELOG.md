# Changelog

All notable changes to `zep-ingest` are documented here. The project follows
[Semantic Versioning](https://semver.org); while at `0.x` the public API may
still change between minor versions.

## 0.1.0 (unreleased)

First release — everything upstream of the Zep API for getting unstructured and
structured data into Context Graphs correctly.

- **A one-liner per source:** `ingest_slack_export`, `ingest_documents`,
  `ingest_transcripts`, `ingest_emails`, and `ingest_json_records` (CSV / JSONL /
  JSON array) parse the source, carry whatever timestamps the source itself
  supplies, and submit.
- **User-graph and explicit paths:** `ingest_thread_messages` backfills chat
  history into a user's graph via threads; `ingest_fact_triples` asserts known
  relationships; `ingest_nodes` seeds canonical entities.
- **Composable pipeline:** `Loader → Transforms → LimitGuard → Submitter`, all
  lazy generators (a 500k-item export never sits in memory). `preview()` returns
  the transformed episodes and validation warnings with zero Zep API calls.
- **Transforms:** `TextChunker` (paragraph/sentence-aware, 500-char chunks),
  `AliasCanonicalizer` (entity-name canonicalization with a risky-word guard),
  and optional `LLMContextualizer` — bring any LLM through a one-method
  `complete()` protocol; OpenAI, Anthropic, and OpenAI-compatible adapters ship
  as optional extras.
- **Submission:** the Batch API by default on every path — episodes and thread
  messages alike — with transparent fallback to sequential `graph.add` /
  `thread.add_messages` when the deployment has no batch endpoint to call
  (HTTP 404), plus rate-limit-aware pacing, retries, progress, and per-item
  error recording. Every ingest call returns as soon as the data is submitted;
  `IngestResult` exposes `wait()` / `status` / `failed_items()` and the resume
  handles (`batch_ids` / `task_ids` / `episode_uuids`).
- **Temporal correctness:** loaders preserve source timestamps when the source
  carries one — Slack `ts` and an email's `Date:` header are used automatically,
  while documents, transcripts, and JSON records stay undated until you supply
  `created_at` / `use_file_mtime=True`, `meeting_start` / `default_start_time`,
  or `created_at_field` respectively. The pipeline warns about episodes missing
  `created_at` before submission, and `search_when_ready` absorbs
  post-ingestion indexing lag.
- **Ingests into existing, configured destinations:** create the graph and set
  its ontology (`client.graph.set_ontology`) yourself first — the package does
  not create graphs or users, nor set ontologies (`ingest_thread_messages`
  creates only the backfill's own threads).
- **Eager, client-side validation:** every documented API limit (episode/message
  size, metadata keys, UUIDs, RFC3339 timestamps, SCREAMING_SNAKE fact names, …)
  is checked before the first network call — a bad item is a clear Python error
  naming the field, not an HTTP 400 mid-run.
- **Canonical Slack names:** speakers, `@mentions`, and DM labels resolve through
  the export roster preferring `profile.real_name` over `profile.display_name`,
  so a workspace handle ("morgan") does not split one person from the full name
  used in other sources ("Morgan Lee"). Authors with no `real_name` are reported
  in `warnings`, and `SlackMessage.user_id` exposes the raw Slack id so
  `formatter=` can substitute names from your own directory.
- **Runnable examples and sample data** for the Slack, document, email,
  JSON-record, thread-backfill, fact-triple, and user-graph paths, built around
  one coherent sample dataset.
