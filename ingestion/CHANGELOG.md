# Changelog

All notable changes to `zep-ingest` are documented here. The project follows
[Semantic Versioning](https://semver.org).

## 0.4.0

### Breaking

- **The package requires the Zep v4 API and the `zep-cloud` 4.x SDK below
  v5.** The dependency moved from `zep-cloud>=3.27.0` to
  `zep-cloud>=4.0.0,<5` (the stable release). The package gives no v3
  compatibility path.
- **Migrating from v3:** follow the
  [V4 migration guide](https://help.getzep.com/migrating-from-v3) for the
  general procedure (the nullable UUID columns, the one-time `lookup`
  backfill, and the order of steps). The API changes that are specific to
  this package:
  - Every ingest one-liner and `Pipeline.run` takes `graph_uuid=` in
    place of `graph_id=`/`user_id=`.
  - `ThreadMessage` takes `thread_uuid` in place of `thread_id`.
  - `NodeItem` no longer accepts `uuid` or `created_at`, and `FactTriple`
    no longer accepts `fact_uuid` or `created_at`; Zep assigns each node
    and fact identity.
  - `IngestResult.node_uuids` and `IngestResult.edge_uuids` return the
    UUIDs that Zep assigned; store them in the application's own records.
    Every other public function name and parameter is unchanged.
  - For an existing V3 user, thread, or graph, do not call
    `client.user.create`, `client.thread.create`, or `client.graph.create`.
    Get its UUID one time with the backfill in the migration guide, and
    store it.

### Changed

- **v4 submission methods.** Episodes go to `graph.episode.add`, batches go to
  `batch.create` + `batch.add_items` + `batch.process`, nodes go to
  `graph.node.add`, and fact triples go to `graph.edge.add` with
  `edges=[EdgeInput]` and `deduplicate=True` to keep the v3
  `add_fact_triple` behavior.
- **Search uses the v4 scope methods.** `search_when_ready` calls
  `graph.search_edges`, `graph.search_nodes`, `graph.search_episodes`,
  `graph.search_observations`, or `graph.search_thread_summaries`, and returns
  the first page of items.
- **Reference time is sent on every path.** v4 renamed the
  developer-supplied `created_at` field to `reference_time`. The package
  sends `Episode.created_at` and `ThreadMessage.created_at` as
  `reference_time` on `graph.episode.add`, on `batch.add_items` items, and
  on `thread.add_messages` messages.
- **Sequential thread ingestion is task-tracked.** v4 `thread.add_messages`
  returns a task, so `wait()` polls the returned task instead of message UUIDs.
- **Page size on list and search calls is `page_size`.** The stable SDK
  renamed the `limit` parameter of each list and search operation to
  `page_size`; `search_when_ready` passes it through.
- **Batch rejections are reported.** `batch.add_items` can reject single
  items of a page in `rejected_items`; the package now counts only the
  accepted items and records each rejection as an `AddError` with its index
  and reason.

## 0.3.1

- `zep-ingest` now requires `zep-cloud>=3.27.0,<4`. The upper bound keeps the package
  on the v3 SDK when `zep-cloud` 4.x is published.

## 0.3.0

- **Submit everything, then wait once.** Multiple files or loaders destined for
  the same graph are submitted together. Sequential vs batch only chooses the
  submit API (`graph.add` vs Batch API); neither waits for one file to finish
  processing before the next is sent. If you do not need to block, submit and
  return — `wait()` stays opt-in.
- `wait()` aligns with [Check data ingestion status](https://help.getzep.com/check-data-ingestion-status):
  Batch API paths poll the last batch via `batch.get`; sequential `graph.add`
  polls the last-submitted episode; sequential `thread.add_messages` polls the
  last message UUID per thread (regular ``add_messages`` returns message UUIDs,
  not ``task_id``); nodes/triples poll every task id.
  Default timeout is `wait_timeout_seconds(items_submitted)` — 60s per item,
  minimum 120s. Pass `timeout=None` to wait without a deadline.
  `IngestResult.from_batch_ids(...).wait()` has no item count, so auto timeout
  does not invent a 120s cap. Do not mix Batch API and sequential `graph.add`
  on the same graph and expect one `wait()` to cover both.
- File one-liners and loaders accept a sequence of paths/globs in caller order
  (`ingest_json_records(client, [issues, prs, jira], graph_id=...)`).
- `ConcatLoader` concatenates heterogeneous loaders into one submit stream.
- `IngestResult.combine(...)` merges poll handles for separate submits to the same
  graph (`batch_ids`, `episode_uuids`, `task_ids`); it does not merge
  `node_uuids` / `edge_uuids` (zip alignment). Prefer separate `wait()` calls
  for seeding vs episode ingest.
- Multi-thread sequential backfills poll the last message UUID **per thread**
  (threads are independent sagas on the server). Regular `thread.add_messages`
  returns `message_uuids` only — not `task_id`.
- Production smoke script: `ingestion/scripts/release_smoke_prod.py` (requires
  `ZEP_API_KEY`; run via KeyBank `zep-prod`).

## 0.2.1

- Batch submission now rolls over at 10,000 items by default
  (`DEFAULT_ITEMS_PER_BATCH`) instead of filling to the API's 50,000-item cap.
  Pass `max_items_per_batch` (up to `MAX_ITEMS_PER_BATCH`) to request a larger
  batch.

## 0.2.0

**Breaking:** Zep assigns node and fact UUIDs server-side. Matches the API
change that rejects caller-supplied node identity and ignores caller-supplied
fact identity.

- `NodeItem` no longer accepts a client `uuid`, and `ingest_nodes` no longer
  has `require_uuids`. Zep assigns node identities and returns them on
  `IngestResult.node_uuids` (parallel to the submitted nodes, with `None` for
  failed batches; also recovered from completed task params when resuming).
- `FactTriple` no longer accepts `fact_uuid`; after `wait()`/`refresh()`,
  assigned fact identities land on `IngestResult.edge_uuids` from task params
  (parallel to submitted triples, with `None` for a terminal task that
  assigned none).
- `source_node_uuid` / `target_node_uuid` remain caller-supplied pins to
  existing nodes.
- JSON row files that still include `uuid` / `fact_uuid` raise a clear
  ConfigurationError naming the retired field.
- Requires `zep-cloud>=3.27.0`.

## 0.1.0

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
