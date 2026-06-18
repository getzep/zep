# Changelog

## 0.1.0 (2026-06-16)

### Added

- `createZepBeforeModelCallback` — primary `beforeModelCallback` factory that persists the latest user message to Zep and injects the retrieved Context Block into `LlmRequest.config.systemInstruction`.
- `createZepAfterModelCallback` — `afterModelCallback` factory that persists assistant responses to the Zep thread, skipping tool-call turns and partial streaming chunks.
- `ZepContextTool` — a `BaseTool` subclass offering the same persist-and-inject behaviour via `processLlmRequest`, as a tool-centric alternative to the callback.
- `ZepGraphSearchTool` — a model-callable `BaseTool` for on-demand knowledge-graph search, with support for user graphs and standalone graphs.
- Identity resolution from explicit options, ADK session state (`zep_user_id`, `zep_thread_id`, `zep_first_name`, `zep_last_name`, `zep_email`), and ADK session metadata.
- Lazy Zep user and thread creation with an "already exists" guard and a per-process cache.
- Graceful error handling: every Zep call is wrapped so a Zep failure is logged but never crashes the host agent.
- Mock-based test suite (Vitest) and two runnable examples.
- Built against `@google/adk@1.2.0` and `@getzep/zep-cloud@^3.23.0` (Zep V3), ESM, NodeNext + strict.
