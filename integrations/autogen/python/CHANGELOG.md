# Changelog

All notable changes to the zep-autogen package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking: the package targets the Zep v4 API and requires `zep-cloud==4.0.0a5`.** Zep v4 assigns the UUID of every user, thread, and graph. The public API takes UUIDs instead of names.
- **Breaking: the resource parameters are renamed.** `ZepUserMemory` takes `user_uuid`, `thread_uuid`, and the optional `graph_uuid`. `ZepGraphMemory` takes `graph_uuid`. `create_search_graph_tool` and `create_add_graph_data_tool` take `graph_uuid` or `user_uuid`. The `ContextInput` dataclass gives `user_uuid`, `thread_uuid`, and `graph_uuid`.
- **Breaking: `ensure_user` and `ensure_thread` are replaced by `create_user` and `create_thread`.** Zep v4 creates a resource and returns its UUID, so the create-then-catch-conflict pattern no longer applies. `create_user` returns the `User` model and `create_thread` returns the `Thread` model. Read `uuid_` from the response and store it in your own database.
- **Breaking: `ZepUserMemory` no longer creates the Zep user.** The application creates the user one time with `create_user`, or with `client.user.create`. `add()` still creates a thread when the constructor did not get a `thread_uuid`.
- **Breaking: `ZepGraphMemory` replaces `facts_limit` and `entity_limit` with `max_characters`.** Context retrieval calls `graph.get_context`, which returns an assembled context block and takes a character budget in place of a fact count and an entity count.
- **Breaking: `ZepUserMemory` replaces `context_template_id` with `context_template_uuid`.** The value is sent as `template_uuid` to `thread.get_context`.
- The default context retrieval calls `thread.get_context(thread_uuid)` in place of the v3 `thread.get_user_context`.
- Message ingestion calls `thread.add_messages(thread_uuid, messages=[...])`.
- Graph ingestion calls `graph.episode.add(graph_uuid, type=..., data=...)` in place of `graph.add`.
- Graph search calls the v4 scope-specific methods `graph.search_edges`, `graph.search_nodes`, `graph.search_episodes`, `graph.search_observations`, and `graph.search_thread_summaries`. The `auto` scope calls `graph.get_context`. Each search method returns a pager, and the integration collects results up to the requested limit.
- Episode retrieval calls `graph.episode.list(graph_uuid)` and iterates the pager.
- Graph deletion calls `graph.delete(graph_uuid)`.
- The search limit is clamped to the range 1 to 50.

### Removed

- The v3 types and helpers that Zep v4 does not give: `GraphSearchResults` and `zep_cloud.graph.utils.compose_context_string`. The package formats the search results itself.

## [1.2.1] - 2026-07-29

### Added

- Added the `py.typed` marker so installed `zep-autogen` packages expose their inline type annotations to PEP 561-compatible type checkers.

## [1.2.0]

### Added

- `ensure_user` / `ensure_thread` -- idempotent, out-of-band helpers (new `zep_autogen.provisioning` module) to provision the Zep user and thread before the first turn. Both return whether the resource was newly created, so callers can drive one-time per-user setup. `ensure_user` accepts an optional `on_created` hook (`UserSetupHook`) that fires only when the user is genuinely new; a genuine failure (auth, network, 5xx) or an `on_created` hook error always propagates when called directly.
- `ZepUserMemory` now lazily provisions **both** the Zep user and thread (previously only the thread was auto-created, and only from `add()`). The lazy path is hot-path-wrapped: a genuine failure or an `on_created` hook error is logged and swallowed so it never raises into `add()`/`update_context()`. New constructor kwargs `first_name`, `last_name`, `email`, and `on_created` feed this path.
- `context_builder` constructor kwarg on `ZepUserMemory` -- an optional async callable that replaces the default `thread.get_user_context(...)` retrieval in `update_context()` with custom logic (e.g. a filtered graph search, a different graph entirely). Receives a single `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`, `model_context`). Unlike the sibling Zep integrations, this never runs concurrently with message persistence -- AutoGen's `Memory` protocol calls `update_context()` (injection) and `add()` (persistence) as two separate, caller-controlled steps, so there is nothing to `asyncio.gather` the builder against.
- `context_template` constructor kwarg on `ZepUserMemory` -- configures the template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block, canonical across zep-adk's Python, Go, and TypeScript implementations). Rendered via plain string replacement, never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject.
- `create_search_graph_tool` gains pin-or-expose control: `scope` (all six of `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto`), `reranker` (`rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`), `limit`, `mmr_lambda`, and `center_node_uuid` are now all model-exposed by default. New `pinned_params`/`hidden_params` keyword arguments fix a parameter to a constant (hidden from the model) or hide it without pinning (Zep's own default applies). `search_filters` and `bfs_origin_node_uuids` are new constructor-only keyword arguments (never exposed to the model).
- New `zep_autogen.limits` module: `truncate_message_content` guards `ZepUserMemory.add`'s thread-message path against Zep's 4,096-char message limit (truncates to 4,000, logging lengths only, never content); `truncate_graph_data` guards `ZepGraphMemory.add` and `create_add_graph_data_tool` against Zep's `graph.add` payload ceiling (truncates to 9,900 chars, matching `zep_ag2.tools.GRAPH_MAX_CHARS`).

### Changed

- **Breaking: `create_search_graph_tool`'s tool schema changed.** AutoGen's `FunctionTool` derives its JSON schema from the wrapped function's typed signature (no raw-JSON-schema escape hatch), so pin-or-expose is implemented by dynamically building that signature. The tool the model sees now exposes `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid` (previously only `limit` and a freeform `scope` string with three documented values). Existing calls to `create_search_graph_tool(client, graph_id=...)` / `create_search_graph_tool(client, user_id=...)` are unaffected; `scope`/`limit` positional-or-keyword legacy arguments still work but now pin (and hide) the corresponding parameter -- see "Migrating from 1.1.x" below.
- **Breaking: `ZepUserMemory.add()` now lazily creates the Zep *user*, not just the thread.** Previously, `add()` only auto-created the thread (via `thread.create`) and never called `user.add`; a message added for a never-provisioned user would 404 inside `thread.add_messages`/`thread.create`. Callers relying on that 404 behavior (e.g. to detect a missing user) will instead see the user silently created. Pass `on_created` if you need one-time setup for newly-created users, or call `ensure_user`/`ensure_thread` out-of-band before constructing `ZepUserMemory` if you need explicit control over provisioning timing.
- `search_memory` (the internal, unexported helper behind the pre-1.2.0 `create_search_graph_tool`) has been removed; its behavior is superseded by the pin-or-expose implementation described above.

### Migrating from 1.1.x

**Pinning search parameters:**

```python
# Before (1.1.x) -- scope only had 3 documented values, limit had no pin/expose control
tool = create_search_graph_tool(
    client, user_id="user-1", scope="nodes", limit=5
)  # positional style

# After (1.2.0) -- legacy kwargs still work as pins, or be explicit:
tool = create_search_graph_tool(client, user_id="user-1", scope="nodes", limit=5)  # still works
tool = create_search_graph_tool(
    client, user_id="user-1", pinned_params={"scope": "nodes", "limit": 5}
)  # equivalent
```

**Provisioning a user before the first turn (optional, but recommended for explicit control):**

```python
from zep_autogen import ensure_user, ensure_thread

await ensure_user(zep_client, user_id="user-1", first_name="Jane", email="jane@example.com")
await ensure_thread(zep_client, thread_id="thread-1", user_id="user-1")

memory = ZepUserMemory(client=zep_client, user_id="user-1", thread_id="thread-1")
```

## [1.1.1]

### Changed
- Target the latest Zep SDK (`zep-cloud>=3.23.0`) and AutoGen (`autogen-agentchat`/`autogen-ext>=0.7.0`).
- `ZepUserMemory.update_context` no longer passes the removed `mode` argument to
  `thread.get_user_context`. In Zep V3 the Context Block is auto-assembled and the
  `"basic"`/`"summary"` modes have been deprecated and removed.

### Removed
- Dropped the `thread_context_mode` constructor parameter (no longer supported by the
  Zep V3 API).

### Added
- New optional `context_template_id` constructor parameter on `ZepUserMemory`. When set,
  it is forwarded as `template_id` to `thread.get_user_context`, enabling custom Context
  Block rendering via Zep context templates (the V3 replacement for summary-vs-raw control).

## [0.1.0] - 2024-01-XX

### Added
- Initial release of zep-autogen integration package
- `ZepMemory` class implementing AutoGen's Memory interface
- Support for persistent conversation memory with Zep Cloud
- Async/await support for modern Python applications
- Comprehensive type hints and documentation
- Basic example demonstrating usage with AutoGen agents
- Error handling for missing dependencies

### Features
- Seamless integration with AutoGen agents
- Intelligent context retrieval from Zep memory
- Support for user-specific and thread-specific memory contexts
- Configurable memory retrieval limits
- Compatible with AutoGen 0.6.1+

[Unreleased]: https://github.com/getzep/zep/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/getzep/zep/releases/tag/v0.1.0
