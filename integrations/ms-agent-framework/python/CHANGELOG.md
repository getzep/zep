# Changelog

## 0.1.0 (2026-06-16)

### Added

- `ZepContextProvider` -- a Microsoft Agent Framework `ContextProvider` that gives an agent long-term memory backed by Zep.
- `before_run` persists the latest user message via `thread.add_messages(return_context=True)` and injects Zep's Context Block into the agent's instructions.
- `after_run` persists the assistant response to the same Zep thread.
- Lazy Zep user and thread creation on first run, cached per provider instance.
- Optional `on_user_created` hook for per-user ontology, custom instructions, or user summary instructions.
- Configurable user/assistant message display names, `ignore_roles`, and `source_id`.
- Graceful error handling that logs failures without crashing the host agent.
- Mock-based test suite plus a live integration test gated on `ZEP_API_KEY` and `OPENAI_API_KEY`.
- Working example demonstrating cross-thread memory recall.
