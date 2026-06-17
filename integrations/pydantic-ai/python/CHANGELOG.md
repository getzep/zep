# Changelog

## 0.1.0 (2026-06-16)

### Added

- `ZepDeps` -- dataclass carrying the Zep client and user/thread identity, used as the agent's `deps_type`.
- `zep_history_processor` -- a Pydantic AI history processor (registered via `capabilities=[ProcessHistory(...)]`) that persists each user turn via `thread.add_messages(return_context=True)` and prepends Zep's context block to the prompt. Dedupes by latest user message to handle `ProcessHistory`'s once-per-model-request invocation, preventing duplicate episodes during tool-calling runs.
- `persist_run` -- helper to persist the assistant reply from `result.new_messages()` to the Zep thread, skipping tool-call scaffolding.
- `create_zep_search_tool` -- factory producing a model-callable `@agent.tool` over `graph.search`, targeting the current user's graph or a standalone graph.
- Lazy Zep user and thread creation on first use.
- Graceful error handling throughout: Zep failures are logged and never crash the agent run; failed persists are retried.
- Mock-based test suite plus end-to-end wiring tests using Pydantic AI's `TestModel`.
- Working example demonstrating fact seeding and memory recall.
