# Changelog

All notable changes to the zep-livekit integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-07

### Added

- `ensure_user` / `ensure_thread` (new `zep_livekit.provisioning` module) -- idempotent, out-of-band helpers to provision the Zep user and thread before the first turn. Both return whether the resource was newly created. `ensure_user` accepts an optional `on_created` hook (`UserSetupHook`) that fires only when the user is genuinely new; a genuine failure or an `on_created` hook error always propagates.
- Lazy resource creation on `ZepUserAgent`: new keyword-only constructor params `first_name`/`last_name`/`email`/`on_created` drive a lazy, instance-cached `ensure_user`/`ensure_thread` call at the top of `on_user_turn_completed`. Unlike calling `ensure_user`/`ensure_thread` directly, this hot path always logs and swallows failures (including an `on_created` hook failure) rather than raising into the voice session.
- `context_builder` / `ContextInput` on `ZepUserAgent` -- an optional async callable that replaces the default `thread.add_messages(return_context=True)` retrieval with custom logic (e.g. a filtered graph search, a different graph entirely). Receives a single `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`, `session`). When set, message persistence and context building run **concurrently**, with per-side failure isolation.
- `context_builder` / `GraphContextInput` (`GraphContextBuilder`) on `ZepGraphAgent` -- an optional async callable that replaces the default hybrid-search retrieval (`_retrieve_graph_context`) entirely. Receives a single `GraphContextInput` (`zep`, `graph_id`, `user_message`, `session`). `ZepGraphAgent` does **not** accept `on_created` (it is scoped to a standalone `graph_id`, not a Zep user) -- passing it raises `TypeError` instead of being silently swallowed.
- `context_template` on both `ZepUserAgent` and `ZepGraphAgent` -- configure the template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block). Rendered via plain string replacement, never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject. Canonical across zep-adk's Python, Go, and TypeScript implementations.
- `create_graph_search_tool` (new `zep_livekit.tools` module) -- a model-callable LiveKit function tool (built via `function_tool(raw_schema=...)`) for searching a Zep knowledge graph on demand, alongside the context injected automatically every turn. Exposes `scope` (six values), `reranker` (five), `limit`, `mmr_lambda`, and `center_node_uuid` in the model-facing schema by default; `pinned_params`/`hidden_params` fix or hide any of them. Exactly one of `graph_id`/`user_id` is required. Register it via `Agent(tools=[...])`.
- Truncation (new `zep_livekit.limits` module, previously absent from this package): `truncate_message_content` is applied before every `thread.add_messages` call in `ZepUserAgent` (both the user-turn path and the `conversation_item_added` assistant path); `truncate_graph_data` (`GRAPH_MAX_CHARS = 9900`) is applied before every `graph.add` call in `ZepGraphAgent`. Prevents a silently-dropped 400 on over-long payloads; logs a warning with lengths only, never content.

### Changed

- **Breaking: the default injected context wording changed.** The hardcoded pre-0.2.0 wrappers -- `"Relevant user context:\n{context}"` in `ZepUserAgent` and `"Relevant knowledge from memory:\n{context}"` in `ZepGraphAgent` -- are replaced by `DEFAULT_CONTEXT_TEMPLATE` (`"The following context is retrieved from Zep, the agent's long-term memory. It contains relevant facts, entities, and prior knowledge about the user. Use it to inform your responses.\n\n<ZEP_CONTEXT>\n{context}\n</ZEP_CONTEXT>"`), now overridable via `context_template`. See "Migrating from 0.1.x" below.
- **Efficiency: `ZepUserAgent`'s default turn path is now a single round-trip.** Previously, `on_user_turn_completed` called `thread.add_messages(...)` and then a separate `thread.get_user_context(...)`. These are now folded into one `thread.add_messages(..., return_context=True)` call. Investigated whether the deprecated `context_mode` parameter needed a two-call fallback to preserve its behavior: it does not -- the Zep V3 `get_user_context` no longer accepts a `mode` argument at all, so `context_mode` was already fully inert before this change, and there is no non-default mode to keep the old two-call path for.

### Migrating from 0.1.x

**Default injected context wording (breaking):**

```python
# Before (0.1.x) -- ZepUserAgent
"Relevant user context:\n{context}"

# Before (0.1.x) -- ZepGraphAgent
"Relevant knowledge from memory:\n{context}"

# After (0.2.0) -- both agents, overridable via context_template
DEFAULT_CONTEXT_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n"
    "<ZEP_CONTEXT>\n{context}\n</ZEP_CONTEXT>"
)
```

If you depend on the exact old wording (e.g. in a prompt-matching test), pass it explicitly via `context_template=...`.

## [0.1.1] - 2026-06-16

### Changed
- Modernized for the latest Zep Python SDK (`zep-cloud>=3.23.0`) and the latest
  `livekit-agents` (1.6.x).
- Removed the deprecated `mode` argument from `thread.get_user_context()` in
  `ZepUserAgent`. The Zep V3 Context Block now returns a structured format and no
  longer supports the `"basic"`/`"summary"` selector.

### Deprecated
- The `context_mode` constructor parameter on `ZepUserAgent` is now ignored. It is
  retained for backwards compatibility and will be removed in a future release.

## [0.1.0] - 2025-01-27

### Added
- Initial release of Zep LiveKit integration
- **Dual Agent Architecture**:
  - `ZepUserAgent`: Thread-based conversational memory for user sessions
  - `ZepGraphAgent`: Knowledge graph-based memory for shared knowledge across sessions
- **Event-Driven Architecture**: Automatic conversation capture using LiveKit's conversation events
- **Message Attribution**: Optional user and assistant message naming for better conversation tracking
- **Hybrid Memory Retrieval**: Graph agent supports parallel search across facts, entities, and episodes
- **User Prefixing**: Graph agent supports optional user name prefixing for multi-user attribution

### Core Features
- **Thread Memory**: Persistent conversation history in Zep threads with context modes (basic/summary)
- **Knowledge Graph**: Shared knowledge storage across conversations with smart context composition
- **Memory Injection**: Automatic context retrieval and injection into LiveKit agent conversations
- **Message Deduplication**: Prevents duplicate message storage using content hashing and IDs
- **Error Handling**: Comprehensive exception handling with graceful degradation
- **Type Safety**: Full type annotations and MyPy compatibility throughout

### Integration Capabilities
- **LiveKit Compatibility**: Drop-in replacement for standard LiveKit Agent
- **Flexible Constructor**: Dynamic `**kwargs` support for all LiveKit Agent parameters
- **Tool Integration**: Function tools that can be mixed into any LiveKit agent
- **OpenAI Integration**: Seamless compatibility with LiveKit's OpenAI plugins
- **Production Ready**: Async/await throughout, proper logging, minimal overhead

### Examples & Documentation
- **Voice Assistant Example**: Complete thread-based memory agent (`voice_assistant.py`)
- **Knowledge Assistant Example**: Graph-based memory agent (`graph_voice_assistant.py`)
- **Tools Examples**: Standalone memory tools integration examples
- **Deployment Guide**: FastAPI and production deployment patterns
- **Comprehensive Documentation**: API reference, usage patterns, and best practices

### Development Infrastructure
- **Quality Assurance**: Ruff formatting, MyPy type checking, comprehensive linting
- **Clean Architecture**: Separation of concerns between storage and retrieval
- **Makefile Workflows**: `make pre-commit`, `make ci` for development consistency
- **No-Test Mode**: Graceful handling of projects without test files

### Dependencies
- `livekit-agents>=0.8.0` - LiveKit agents framework
- `zep-cloud>=3.4.3` - Zep Cloud client library  
- `typing-extensions>=4.0.0` - Type hints compatibility

### Architecture Decisions
- **Event-Driven Storage**: Uses LiveKit's `conversation_item_added` events for real-time capture
- **Dual Memory Strategy**: Thread memory for conversations, graph memory for knowledge
- **Per-User Agent Instances**: Designed for typical deployment where each user gets their own agent
- **Minimal Logging**: Clean, production-ready logging with essential information only

[Unreleased]: https://github.com/getzep/zep/compare/zep-livekit-python-v0.2.0...HEAD
[0.2.0]: https://github.com/getzep/zep/compare/zep-livekit-python-v0.1.1...zep-livekit-python-v0.2.0
[0.1.0]: https://github.com/getzep/zep/releases/tag/zep-livekit-v0.1.0