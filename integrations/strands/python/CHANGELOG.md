# Changelog

## 1.0.0

### Breaking

To migrate from Zep v3, follow the
[V3 to V4 migration guide](https://help.getzep.com/migrating-from-v3) for the
general procedure. The changes below are specific to this package.

- The package requires the Zep v4 SDK (`zep-cloud>=4.0.0,<5`).
- `ZepMemoryStore`: the `user_id`, `thread_id`, and `graph_id` parameters are
  renamed to `user_uuid`, `thread_uuid`, and `graph_uuid`. Give the stored user
  UUID and thread UUID to `user_uuid` and `thread_uuid` in user-graph mode, or
  the stored graph UUID to `graph_uuid` in standalone-graph mode.
- `create_zep_search_tool`: the `user_id` and `graph_id` parameters are renamed
  to `user_uuid` and `graph_uuid`. Give the stored user UUID or graph UUID.
- `ensure_user` and `ensure_thread` are removed. For a new user or thread, use
  `create_user` and `create_thread`. They return the created `User` and
  `Thread`, and you read `uuid_` from each. `create_user` has no `user_id`
  parameter. `create_thread` takes `user_uuid` instead of `thread_id` and
  `user_id`. The `on_created` hook of `create_user` receives the user UUID.
- `ZepMemoryStore` does not create a user or a thread. Its `email` and
  `on_user_created` parameters are removed. Give `email` and `on_created` to
  `create_user`.

For an existing V3 user, thread, or graph, do not call `create_user`,
`create_thread`, or `graph.create`. Get its UUID one time with the backfill in
the migration guide, and store it.

### Changed

- `search` now calls `graph.get_context` for `scope="auto"` and the scoped
  `graph.search_edges`, `graph.search_nodes`, `graph.search_episodes`,
  `graph.search_observations`, and `graph.search_thread_summaries` methods for
  the other scopes. A scoped search returns a pager, and the store reads the
  items of the first page.
- `add` now calls `graph.episode.add`.
- An unknown message role now becomes `user`. Zep v4 accepts the roles
  `user`, `assistant`, `system`, `tool`, and `function`.

### Added

- `create_user` and `create_thread` provisioning helpers for Zep v4.
- Live agent integration test (`test_integration_full_lifecycle`) exercising
  `Agent` + `MemoryManager` + `ZepMemoryStore` against Zep Cloud and OpenAI,
  including cross-thread recall and the `create_user` `on_created` hook (gated on
  `ZEP_API_KEY` + `OPENAI_API_KEY`). Store-only round-trip remains available
  with just `ZEP_API_KEY`.

### Fixed

- The search tool and the store now send only the scope and reranker pairs
  that the Zep v4 API accepts. The tool schema shows only the rerankers that
  the scope supports. An invalid pair from the model gives a clear error that
  names the setting and the valid values. An invalid pair in the constructor
  raises a `ValueError`. The `mmr` reranker sends `mmr_lambda=0.5` when no
  value is given.
- The `auto` scope takes no reranker. A reranker from the model with
  `scope="auto"` gives a clear error, and the tool does not call Zep. A pinned
  reranker with `scope="auto"`, or a `search_reranker` with
  `search_scope="auto"` on `ZepMemoryStore`, raises a `ValueError`. The
  `reranker` tool parameter has no schema default. When the model gives no
  reranker for a scoped search, the tool sends `rrf`, as before.
- `ZepMemoryStore.initialize()` no longer calls Zep. `Agent.__init__` is
  synchronous, so Strands runs that hook on a throwaway event loop in a worker
  thread; provisioning there drove the caller's `AsyncZep` client from a second
  event loop and raised `RuntimeError: ... is bound to a different event loop`
  whenever the caller had already awaited that client (the pattern the README
  and example recommend: create the resources, then hand the client to the
  store). The store now makes its first Zep call on the first search or write,
  which always runs on the agent's own loop.
- `ZepMemoryStore` now rejects `extraction=True` (or an `ExtractionConfig`) at construction unless the store is writable user-graph mode with both `user_uuid` and `thread_uuid`, so `MemoryManager` never schedules extraction that would raise on every cycle.
- `add()` no longer truncates oversized `json` payloads. Slicing JSON strips its closing syntax, so the size guard produced a document Zep would reject; oversized `json` now raises a `ValueError` pointing at chunking. `text`/`message` payloads are still truncated with a warning.
- Corrected the `provisioning` module docstring (it incorrectly referred to
  `ZepContextProvider`).
- `zep_search` no longer returns raw exception text to the model. SDK errors
  can include URLs, identifiers, or response bodies; the tool now returns a
  fixed `"Graph search failed."` string and logs only the exception type
  (and `status_code` when present), with the full traceback at DEBUG.
- The example and live agent test now pass an explicit `OpenAIModel`. Both
  require `OPENAI_API_KEY` and document `strands-agents[openai]`, but neither
  passed `model=` to `Agent`, so Strands silently fell back to its
  `BedrockModel()` default and the run failed on missing AWS credentials. The
  model ID is overridable via `OPENAI_MODEL` (default `gpt-5-mini`).

### Changed

- Documented that Strands' default extraction cadence is every **5 turns**, so conversation batches only reach Zep when the trigger fires (or on `flush()`), delaying graph building relative to turn-by-turn persistence — in addition to Zep's asynchronous ingestion after messages arrive.
- Documented the failure-handling contract: Zep SDK errors propagate out of `search`/`add`/`add_messages` by design, because `MemoryManager` and `ExtractionCoordinator` own failure isolation (skip-and-log, `AggregateMemoryError`, and high-water-mark rollback for retry). Added tests pinning that behavior.
- Example and live tests flush at the session boundary after `invoke_async`,
  which `MemoryManager` requires to persist buffered turns on that path.

## 0.1.1 (2026-10-01)

### Changed

- The package now requires `zep-cloud>=3.23.0,<4`. The upper bound keeps the package on the v3 SDK when `zep-cloud` 4.x is published.

## 0.1.0 (2026-08-01)

### Added

- `ZepMemoryStore` — a Strands Agents `MemoryStore` backed by Zep's temporal Context Graph.
- `search` via `graph.search` (default `scope="auto"` returns Zep's assembled Context Block).
- `add_messages` via `thread.add_messages` for server-side extraction in user-graph mode.
- `add` via `graph.add` for text/JSON/message facts (`metadata["type"]` selects the data type).
- `initialize` is a no-op. The store provisions the Zep user and thread on the first search or write (no provisioning for standalone graphs).
- Standalone-graph mode via `graph_id` (search + add only).
- `ensure_user` / `ensure_thread` idempotent provisioning helpers with optional `on_created` hook.
- `create_zep_search_tool` with pin-or-expose control over `graph.search` parameters.
- `expose_search_tool` on `ZepMemoryStore` to register the search tool through `get_tools()`.
- Message and graph payload truncation helpers with length-only warnings.
- Mock-based unit tests, a gated live integration test, and a runnable example.
- Live agent integration test (`test_integration_full_lifecycle`) exercising
  `Agent` + `MemoryManager` + `ZepMemoryStore` against Zep Cloud and OpenAI,
  including cross-thread recall and `on_user_created` (gated on
  `ZEP_API_KEY` + `OPENAI_API_KEY`). Store-only round-trip remains available
  with just `ZEP_API_KEY`.

### Fixed

- `ZepMemoryStore.initialize()` no longer calls Zep. `Agent.__init__` is
  synchronous, so Strands runs that hook on a throwaway event loop in a worker
  thread; provisioning there drove the caller's `AsyncZep` client from a second
  event loop and raised `RuntimeError: ... is bound to a different event loop`
  whenever the caller had already awaited that client (the pattern the README
  and example recommend: `ensure_user`, then hand the client to the store).
  Provisioning now happens on the store's first search or write, which always
  runs on the agent's own loop.
- `ZepMemoryStore` now rejects `extraction=True` (or an `ExtractionConfig`) at construction unless the store is writable user-graph mode with both `user_id` and `thread_id`, so `MemoryManager` never schedules extraction that would raise on every cycle.
- `add()` no longer truncates oversized `json` payloads. Slicing JSON strips its closing syntax, so the size guard produced a document Zep would reject; oversized `json` now raises a `ValueError` pointing at chunking. `text`/`message` payloads are still truncated with a warning.
- Corrected the `provisioning` module docstring (it incorrectly referred to
  `ZepContextProvider`).
- `zep_search` no longer returns raw exception text to the model. SDK errors
  can include URLs, identifiers, or response bodies; the tool now returns a
  fixed `"Graph search failed."` string and logs only the exception type
  (and `status_code` when present), with the full traceback at DEBUG.
- The example and live agent test now pass an explicit `OpenAIModel`. Both
  require `OPENAI_API_KEY` and document `strands-agents[openai]`, but neither
  passed `model=` to `Agent`, so Strands silently fell back to its
  `BedrockModel()` default and the run failed on missing AWS credentials. The
  model ID is overridable via `OPENAI_MODEL` (default `gpt-5-mini`).

### Changed

- Documented that Strands' default extraction cadence is every **5 turns**, so conversation batches only reach Zep when the trigger fires (or on `flush()`), delaying graph building relative to turn-by-turn persistence — in addition to Zep's asynchronous ingestion after messages arrive.
- Documented the failure-handling contract: Zep SDK errors propagate out of `search`/`add`/`add_messages` by design, because `MemoryManager` and `ExtractionCoordinator` own failure isolation (skip-and-log, `AggregateMemoryError`, and high-water-mark rollback for retry). Added tests pinning that behavior.
- Example and live tests flush at the session boundary after `invoke_async`,
  which `MemoryManager` requires to persist buffered turns on that path.
