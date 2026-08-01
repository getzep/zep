# Changelog

## Unreleased

### Fixed

- `ZepMemoryStore` now rejects `extraction=True` (or an `ExtractionConfig`) at construction unless the store is writable user-graph mode with both `user_id` and `thread_id`, so `MemoryManager` never schedules extraction that would raise on every cycle.
- `add()` no longer truncates oversized `json` payloads. Slicing JSON strips its closing syntax, so the size guard produced a document Zep would reject; oversized `json` now raises a `ValueError` pointing at chunking. `text`/`message` payloads are still truncated with a warning.

### Changed

- Documented that Strands' default extraction cadence is every **5 turns**, so conversation batches only reach Zep when the trigger fires (or on `flush()`), delaying graph building relative to turn-by-turn persistence — in addition to Zep's asynchronous ingestion after messages arrive.
- Documented the failure-handling contract: Zep SDK errors propagate out of `search`/`add`/`add_messages` by design, because `MemoryManager` and `ExtractionCoordinator` own failure isolation (skip-and-log, `AggregateMemoryError`, and high-water-mark rollback for retry). Added tests pinning that behavior.

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
