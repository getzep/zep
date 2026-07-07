# Changelog

All notable changes to `zep-ingest` are documented here.

## Unreleased

- Added: `search_when_ready` — graph search that absorbs the indexing lag
  after ingestion, replacing hand-rolled poll loops.
- Added: `thread_id_suffix=` on `ingest_thread_messages` to namespace
  backfilled thread ids (they are global to a project).
- Added: `DEFAULT_RISKY_WORDS` exported starter set for the alias guard.
- Fixed: `LimitGuard` yielded the original over-limit episode (and a stale
  "was split" warning) when splitting collapsed it into a single piece.
- Fixed: aliases that start or end with punctuation (".NET", "C++") never
  matched; the word-boundary regex now uses lookarounds.
- Fixed: an explicitly-passed empty `risky_words` set disabled the guard
  instead of arming the minimum-length check.
- Fixed: the sequential thread path dropped per-message `metadata`.
- Fixed: `thread.create` failures were swallowed as "already exists" for any
  400; only genuine already-exists responses are tolerated now.
- Fixed: the auto batch→sequential fallback no longer re-submits everything
  when earlier batches were already accepted (duplicate-ingestion guard).
- Fixed: sequential thread ingestion now warns that wait()/status reflect
  submission only (extraction has no completion handle on that path).
- Fixed: a transient error on `batch.process` no longer crashes the run and
  discards the result — it is retried, and a persistent failure is recorded
  as an `AddError` with the batch pinned "failed" (terminal for `wait()`),
  keeping batch ids usable for a manual retry.
- Fixed: `JsonNormalizer` emitted an empty `{}` episode when an identity-less
  record was fully consumed by list explosion / long-string extraction.
- Fixed: non-string timestamps (e.g. an epoch number in a JSONL row) now fail
  validation with a clear error naming the field instead of an
  `AttributeError`.
- Fixed: loader `warnings` (e.g. `JsonRecordsLoader`'s unparseable
  `created_at_field` notice) are now collected into preview/run warnings the
  same way transform warnings are.
- Added: `IngestResult.from_batch_ids(client, batch_ids)` — reconstruct a
  result from persisted batch ids to check status in a later process.
- Added: `skip_subtypes=` on `ingest_slack_export` (the loader parameter,
  now plumbed through the one-liner).
- Added: `risky_words=` on `AliasCanonicalizer` and the `aliases`-accepting
  one-liners. The risky-alias guard is now opt-in: no `risky_words`, no guard.
- Added: `ingest_fact_triples` / `ingest_thread_messages` file paths now also
  accept JSON-array files (previously JSONL/CSV only).
- Added: `source_node_labels` / `target_node_labels` on `FactTriple` — triples
  skip extraction, so labels are the only way triple-created nodes join the
  declared ontology (at most one entity-type label per node, validated).
- Changed: starter ontology (`examples/example_ontology.py`) — sharper
  exclusions on Person/Project/Product (no people-groups, market segments, or
  generic device categories) and a new `CUSTOMER_OF` edge.
- Fixed: `ingest_thread_messages(method="auto")` now submits sequentially
  when messages carry `created_at` — the Zep Batch API currently ignores
  `created_at` on `thread_message` items, silently dating backfills at
  ingestion time (verified live; `graph_episode` items are unaffected).
  Explicit `method="batch"` warns about the timestamp loss.
- Removed: `AliasCanonicalizer`'s hardcoded `RISKY_ALIAS_WORDS` list and
  `allow_risky_aliases=` parameter — the copyable word list now lives in
  `examples/slack_export_example.py`.
- Removed: the `zep_ingest.inspect` module (`type_coverage_report` /
  `CoverageReport`) — it was an internal ontology-iteration dev tool.
- Renamed: `create_missing=` → `create_if_missing=` on `Pipeline.run` and the
  one-liners (it creates the destination graph *or* user when absent).
- Changed: examples are now self-contained and zero-argument — each creates a
  fresh timestamped graph, sets the starter ontology, and ingests bundled
  sample data (`examples/data/`, the fictional Meridian Robotics world);
  `seed_graph_example.py` removed (every example now shows the lifecycle),
  `fact_triples_example.py` and `user_graph_example.py` added.

## 0.1.0 - 2026-07-06

Initial release.

- Pipeline core: `Loader` → `Transform`s → always-on `LimitGuard` → `Submitter`,
  all lazy generators; `preview()` dry-run with warnings before any API call.
- Loaders: `SlackExportLoader` (zip/dir, thread grouping, markup normalization,
  original timestamps), `TextFileLoader`, `EmlLoader` (.eml email exports dated
  by their Date headers), `JsonRecordsLoader` (JSONL/CSV/JSON array with
  identity-field mapping and `created_at` parsing).
- Transforms: `TextChunker` (docs-cookbook paragraph/sentence chunking),
  `LLMContextualizer` (contextual retrieval with untrusted-content hardening;
  bring-your-own LLM via the `LLMClient` protocol, with OpenAI/Anthropic
  adapters and an `OpenAICompatibleLLM` universal connector for LiteLLM/
  Ollama/vLLM/any OpenAI-compatible endpoint), `AliasCanonicalizer` (rewrite/annotate
  with risky-alias guard and replacement accounting), `JsonNormalizer` (the
  docs' JSON-shaping algorithm), `LimitGuard` (10k-char safety net).
- Submitters: `BatchSubmitter` (enterprise Batch API: paging, batch rollover,
  retries) and `SequentialSubmitter` (`graph.add` with Retry-After/backoff);
  `method="auto"` falls back automatically when the Batch API is unavailable.
- `IngestResult` unified over both paths: `status`, `wait()`, `failed_items()`,
  `warnings`, `raise_for_status()`.
- Preflights: `ontology=` (set before data flows — it is not retroactive) and
  `create_missing=` (graph/user creation); missing-`created_at` warnings.
- Fact triples: `FactTriple` with eager client-side validation of every
  documented limit + `ingest_fact_triples` (iterable or JSONL/CSV).
- `type_coverage_report`: declared-vs-derived typing metrics for ontology
  iteration.
- One-liners: `ingest_slack_export`, `ingest_documents`, `ingest_emails`,
  `ingest_json_records`, `ingest`.
- User data: `ThreadMessage` + `ingest_thread_messages` — chat-history
  backfills onto user graphs via Batch API `thread_message` items with a
  `thread.add_messages` fallback; auto-creates users/threads, splits
  messages over the 4,096-char limit, preserves per-thread order.
- Examples ship a starter ontology (`examples/example_ontology.py`) built with
  the documented typing levers; every example applies it via the `ontology=`
  preflight.
