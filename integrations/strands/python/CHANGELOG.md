# Changelog

## Unreleased

### Added

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

## 0.1.0 (2026-08-01)

### Added

- `ZepMemoryStore` — a Strands Agents `MemoryStore` backed by Zep's temporal Context Graph.
- `search` via `graph.search` (default `scope="auto"` returns Zep's assembled Context Block).
- `add_messages` via `thread.add_messages` for server-side extraction in user-graph mode.
- `add` via `graph.add` for text/JSON/message facts (`metadata["type"]` selects the data type).
- `initialize` provisions the Zep user and thread (no-op for standalone graphs).
- Standalone-graph mode via `graph_id` (search + add only).
- `ensure_user` / `ensure_thread` idempotent provisioning helpers with optional `on_created` hook.
- `create_zep_search_tool` with pin-or-expose control over `graph.search` parameters.
- `expose_search_tool` on `ZepMemoryStore` to register the search tool through `get_tools()`.
- Message and graph payload truncation helpers with length-only warnings.
- Mock-based unit tests, a gated live integration test, and a runnable example.
