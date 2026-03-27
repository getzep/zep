# Changelog

## 0.1.0 (2026-03-23)

### Added

- `ZepContextTool` -- ADK `BaseTool` subclass that persists user messages to Zep and injects memory context into LLM prompts via `process_llm_request()`.
- `create_after_model_callback` -- Factory function that returns an `after_model_callback` for persisting assistant responses to Zep.
- Lazy Zep user and thread creation on first use.
- Message deduplication to prevent double-persistence during tool-use cycles.
- Graceful error handling that logs failures without crashing the agent.
- Comprehensive test suite with mocked dependencies.
- Working example demonstrating fact seeding and memory recall.
