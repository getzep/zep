# Changelog

## 0.2.0 (2026-04-07)

### Added

- `auto` scope for `ZepGraphSearchTool` -- lets Zep decide the best mix of edges, nodes, and episodes to return.
- Support for pinning optional parameters to `None` to hide them from the model schema without passing them to the SDK.

### Changed

- Improved `scope` parameter descriptions to be more precise about what each scope searches.

## 0.1.0 (2026-03-23)

### Added

- `ZepContextTool` -- ADK `BaseTool` subclass that persists user messages to Zep and injects memory context into LLM prompts via `process_llm_request()`.
- `create_after_model_callback` -- Factory function that returns an `after_model_callback` for persisting assistant responses to Zep.
- Lazy Zep user and thread creation on first use.
- Message deduplication to prevent double-persistence during tool-use cycles.
- Graceful error handling that logs failures without crashing the agent.
- Comprehensive test suite with mocked dependencies.
- Working example demonstrating fact seeding and memory recall.
