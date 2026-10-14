# Changelog

## 1.0.0

### Breaking

Follow the [V4 migration guide](https://help.getzep.com/migrating-from-v3) for the general procedure: the nullable UUID columns, the one-time `lookup` backfill, and the order of the steps.

The changes that are specific to this package are:

- The package requires `zep-cloud>=4.0.0,<5`. There is no V3 compatibility layer.
- `ensure_user` is replaced by `create_user(client, *, first_name=None, last_name=None, email=None)`. The `user_id` parameter is removed. The function returns the `User`; store `user.uuid_` and `user.graph_uuid`.
- `ensure_thread` is replaced by `create_thread(client, *, user_uuid)`. The `thread_id` and `user_id` parameters are removed. The function returns the `Thread`; store `thread.uuid_`.
- The `on_created` parameter of `ensure_user` and the `UserSetupHook` type are removed. Run one-time user setup after `create_user` returns.
- The session-state key `zep_user_id` is now `zep_user_uuid`, and `zep_thread_id` is now `zep_thread_uuid`. The new optional key `zep_graph_uuid` takes the stored `user.graph_uuid`. `ZepContextTool`, `create_after_model_callback`, and `ZepGraphSearchTool` read these keys. They use the ADK `user_id` and `session_id` only when no key is set, so those values must then be the Zep UUIDs.
- `ContextInput.user_id` is now `ContextInput.user_uuid`, and `ContextInput.thread_id` is now `ContextInput.thread_uuid`. A custom `context_builder` must call the V4 SDK, for example `ctx.zep.graph.search_edges(graph_uuid, query=ctx.user_message)` in place of `ctx.zep.graph.search(user_id=ctx.user_id, ...)`.
- The `graph_id` parameter of `ZepGraphSearchTool` is now `graph_uuid`.
- `ZepMemoryService.search_memory` reads the ADK `user_id` as the Zep user UUID. Set the ADK `user_id` to the stored user UUID, or give a fixed graph UUID to the new `graph_uuid` parameter of `ZepMemoryService`.

For an existing V3 user or thread, do not call `create_user` or `create_thread`. Get its UUID one time with the backfill in the migration guide, and store it.

### Changed

- `ZepGraphSearchTool` and `ZepMemoryService` now call the v4 split search methods (`graph.search_edges`, `search_nodes`, `search_episodes`, `search_observations`, `search_thread_summaries`) and `graph.get_context` for the `auto` scope. The supported scopes are unchanged. The v4 search methods return a pager; the tools read the items of the first page. The `limit` setting of the tools is sent to the SDK as `page_size`.
- `ZepContextTool` and the after-model callback now call `thread.add_messages(thread_uuid, ...)` with `zep_cloud.AddMessage` objects, and `thread.get_context` replaces `thread.get_user_context`.
- `ZepMemoryService` takes an optional `graph_uuid` and otherwise resolves the graph UUID of the user through `user.get`.

## 0.3.1 (2026-07-29)

### Added

- Added the `py.typed` marker so installed `zep-adk` packages expose their inline type annotations to PEP 561-compatible type checkers.

## 0.3.0 (2026-07-06)

### Added

- `ensure_user` / `ensure_thread` -- idempotent, out-of-band helpers to provision the Zep user and thread before the first turn. Both return whether the resource was newly created, so callers can drive one-time per-user setup. `ensure_user` accepts an optional `on_created` hook (`UserSetupHook`) that fires only when the user is genuinely new.
- `ContextInput` -- a frozen dataclass bundling everything a custom `context_builder` needs (`zep`, `user_id`, `thread_id`, `user_message`, `tool_context`, `llm_request`), replacing the old 4-positional-argument builder signature.
- `context_template` on `ZepContextTool` -- configure the template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block). Rendered via plain string replacement, never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject. Canonical across zep-adk's Python, Go, and TypeScript implementations.
- `thread_summaries` added to `ZepGraphSearchTool`'s and `ZepMemoryService`'s supported scopes, alongside `edges`, `nodes`, `episodes`, `observations`, and `auto` -- the full six-scope Zep enum.
- `ZepMemoryService` -- an ADK-native `BaseMemoryService` implementation for wiring Zep into `Runner`'s memory service, so the model can search Zep on demand via ADK's built-in `load_memory`/`preload_memory` tools. `add_session_to_memory` is an intentional no-op, since Zep already ingests conversation turns live via `ZepContextTool` / `create_after_model_callback`.

### Changed

- **Breaking: lazy creation removed.** `ZepContextTool.process_llm_request()` no longer calls `user.add`/`thread.create`. Provision the user and thread explicitly with `ensure_user`/`ensure_thread` before the first turn -- see the README's "Migrating from 0.2.x" section for the full recipe.
- **Breaking: `on_user_created` moved.** The `ZepContextTool(..., on_user_created=...)` constructor argument is removed; pass the hook to `ensure_user(on_created=...)` instead.
- **Breaking: the `zep_email` session-state key removed.** With lazy creation gone, the turn path never touches the Zep user profile, so the key was silently inert. Pass `email` to `ensure_user` during provisioning instead.
- **Breaking: `ContextBuilder` signature changed.** Custom context builders now receive a single `ContextInput` argument instead of four positional arguments (`zep_client`, `user_id`, `thread_id`, `user_message`). See the README migration section for the before/after.
- **Breaking: the default injected context wording changed.** The hardcoded pre-0.3.0 header (`"The following context is retrieved from Zep's long-term memory service. It contains relevant facts, relationships, and prior knowledge about the user. Use it to inform your responses."`) is replaced by `DEFAULT_CONTEXT_TEMPLATE` (`"The following context is retrieved from Zep, the agent's long-term memory. It contains relevant facts, entities, and prior knowledge about the user. Use it to inform your responses."`), now overridable via `context_template`. The `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper is unchanged. See the README's "Migrating from 0.2.x" section for the before/after.
- Raised the minimum `google-adk` requirement to `>=1.19.0,<3` (from `>=1.0.0`) -- the public `ReadonlyContext.user_id`/`.session` properties used internally require `>=1.19.0`; `2.x` is supported but untested beyond `2.3.0`.
- `context_tool.py`, `callbacks.py`, and `graph_search_tool.py` now read the public `ReadonlyContext.user_id` / `.session` properties instead of the private `_invocation_context` attribute, with identical fallback behavior (state-key overrides still take precedence).

## 0.2.1 (2026-06-16)

### Changed

- Raised the minimum `zep-cloud` requirement to `>=3.23.0` to align with the latest Zep V3 SDK. No code changes were required; the integration remains compatible with `google-adk` 2.x.

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
