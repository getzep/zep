# Changelog

All notable changes to the zep-ag2 package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0]

AG2 has no native memory interface -- `ConversableAgent.register_hook` is its only per-turn
seam. This release brings `zep-ag2` up to the standardization bar set by the other Zep
framework integrations: out-of-band provisioning, a pluggable context builder, a
configurable context template, a pin-or-expose tool schema, and (new for this package) an
optional fully-automatic memory loop built on that hook seam.

### Added

- `ensure_user` / `ensure_thread` -- idempotent, out-of-band helpers (new `zep_ag2.provisioning`
  module) to provision the Zep user and thread before the first turn. Both return whether the
  resource was newly created. `ensure_user` accepts an optional `on_created` hook
  (`UserSetupHook`) that fires only when the user is genuinely new; a genuine failure (auth,
  network, 5xx) or an `on_created` hook error always propagates when called directly.
- `ZepMemoryManager` gains keyword-only constructor arguments `first_name`, `last_name`,
  `email`, and `on_created`, feeding a new lazy provisioning path
  (`await manager.ensure_user_and_thread()`). The lazy path is hot-path-wrapped: a genuine
  failure or an `on_created` hook error is logged and returns `False`, never raised into
  `process_user_message`/`get_memory_context`/`enrich_system_message`/`add_messages`/the
  `attach_to_agent` hooks.
- `ZepMemoryManager.attach_to_agent(agent)` -- **new automatic memory loop**, AG2's answer to
  the sibling ports' native per-turn hooks. Registers two `ConversableAgent.register_hook`
  callbacks:
  - `process_last_received_message`: persists every message the agent receives and refreshes
    its system message with fresh context (bridged via the package's existing `_run_sync`
    background loop).
  - `process_message_before_send`: persists every message the agent sends as an `assistant`
    turn. AG2's contract here (`hook(sender=..., message=..., recipient=..., silent=...)`,
    returning the message) is clean enough to complete the loop -- previously, in this
    package, assistant-reply persistence required a manual `add_messages()` call.
  Both hooks return their input unmodified and swallow all internal failures, so a Zep outage
  never breaks the agent's conversation loop. `attach_to_agent` is additive: the existing
  manual `enrich_system_message`/`add_messages` methods are unchanged and still work standalone.
  **Caveat:** attach it to exactly one agent per Zep thread (normally the user-facing agent) --
  if two agents in a conversation both attach managers pointing at the same `session_id`, each
  turn is persisted twice with conflicting roles (one agent's outgoing hook persists it as
  `assistant`, the other's incoming hook persists the same content as `user`). See the README's
  "Multi-agent caveat" section for the correct wiring (attach to one agent, or use distinct
  `session_id`s per attached agent).
- `ZepMemoryManager.process_user_message(user_message, *, agent=None)` -- persists a user turn
  and retrieves context in one call; the manager's own per-turn seam (AG2 has no
  framework-owned equivalent). Requires `session_id`. When `context_builder` is set,
  persistence (`thread.add_messages` *without* `return_context`) and the builder run
  concurrently via `asyncio.gather(..., return_exceptions=True)` with per-side isolation --
  a builder failure never blocks persistence, a persistence failure never blocks the
  builder's result from being returned. When unset, a single
  `thread.add_messages(..., return_context=True)` round-trip does both.
- `context_builder` constructor kwarg on `ZepMemoryManager` -- an optional async callable
  that replaces the default Context Block retrieval used by `process_user_message`,
  `get_memory_context`, and `enrich_system_message`. Receives a single `ContextInput` (`zep`,
  `user_id`, `thread_id`, `user_message`, `agent`) -- `agent` is the AG2 agent in scope when
  invoked via `attach_to_agent`'s automatic loop, `None` otherwise.
- `context_template` constructor kwarg on `ZepMemoryManager` -- configures the template
  wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`, an explicit
  `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block, canonical across zep-adk's Python, Go, and
  TypeScript implementations). Rendered via plain string replacement, never `str.format`, so
  context text containing `{`, `}`, or `%` is always safe to inject.
- `create_search_graph_tool` and `create_search_memory_tool` gain pin-or-expose control:
  `scope` (all six of `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`,
  `auto`), `reranker` (`rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`),
  `limit`, `mmr_lambda`, and `center_node_uuid` are now all model-exposed by default. New
  `pinned_params`/`hidden_params` keyword arguments fix a parameter to a constant (hidden
  from the model) or hide it without pinning (Zep's own default applies). `search_filters`
  and `bfs_origin_node_uuids` are new constructor-only keyword arguments (never exposed to
  the model).

### Changed

- **Breaking: `create_search_graph_tool` / `create_search_memory_tool`'s tool schema
  changed.** AG2's `Tool`/`register_for_llm` derives its schema from the wrapped function's
  typed signature (`inspect.signature` + `typing.get_type_hints`), so pin-or-expose is
  implemented by dynamically building that signature, the same approach as
  `zep_autogen.tools`. The tools the model sees now expose `scope`, `reranker`, `limit`,
  `mmr_lambda`, and `center_node_uuid` (previously only `limit` and a freeform `scope`
  string with three documented values). Existing calls with `scope=`/`limit=` still work,
  but now pin (and hide) the corresponding parameter -- see "Migrating from 0.1.x" below.
  Result formatting is now scope-aware: only the requested scope's results are rendered
  (previously all three of edges/nodes/episodes were concatenated regardless of the
  requested scope).
- **Breaking: `ZepMemoryManager`'s lazy provisioning now creates the Zep *user*, not just the
  thread.** Previously, no method auto-created the Zep user; a message added for a
  never-provisioned user could fail inside `thread.add_messages`. `process_user_message`,
  `get_memory_context`, `enrich_system_message`, and `add_messages` now all call
  `ensure_user_and_thread()` first. Callers relying on the old failure behavior (e.g. to
  detect a missing user) will instead see the user silently created. Pass `on_created` if you
  need one-time setup for newly-created users, or call `ensure_user`/`ensure_thread`
  out-of-band before constructing `ZepMemoryManager` for explicit control over provisioning
  timing.
- `ZepMemoryManager`'s constructor keyword arguments (`first_name`, `last_name`, `email`,
  `on_created`, `context_builder`, `context_template`) are keyword-only.

### Migrating from 0.1.x

**Pinning search parameters:**

```python
# Before (0.1.x) -- scope only had 3 documented values, limit had no pin/expose control
tool = create_search_graph_tool(zep_client, user_id="user-1", scope="nodes", limit=5)

# After (0.2.0) -- legacy kwargs still work as pins, or be explicit:
tool = create_search_graph_tool(zep_client, user_id="user-1", scope="nodes", limit=5)  # still works
tool = create_search_graph_tool(
    zep_client, user_id="user-1", pinned_params={"scope": "nodes", "limit": 5}
)  # equivalent
```

**Provisioning a user before the first turn (optional, but recommended for explicit control):**

```python
from zep_ag2 import ensure_user, ensure_thread

await ensure_user(zep_client, user_id="user-1", first_name="Jane", email="jane@example.com")
await ensure_thread(zep_client, thread_id="thread-1", user_id="user-1")

manager = ZepMemoryManager(zep_client, user_id="user-1", session_id="thread-1")
```

**Adopting the automatic memory loop (optional):**

```python
from zep_ag2 import ZepMemoryManager

manager = ZepMemoryManager(zep_client, user_id="user-1", session_id="thread-1")
manager.attach_to_agent(assistant)  # replaces manual enrich_system_message/add_messages calls
```

## [0.1.0] - 2026-06-17

### Added
- Initial release of zep-ag2 integration package
- `ZepMemoryManager` for system message injection and conversation memory
- `ZepGraphMemoryManager` for knowledge graph operations
- Tool factories: `create_search_memory_tool`, `create_add_memory_tool`,
  `create_search_graph_tool`, `create_add_graph_data_tool`
- `register_all_tools` convenience function for bulk tool registration
- Sync tool execution via background event loop (AG2 calls tools synchronously)
- Async manager classes with sync wrappers for non-async usage
- Comprehensive test suite with >90% coverage
- Examples for basic, graph, search-only, and full tool usage

### Features
- AG2 decorator-compatible tools (`@register_for_llm` / `@register_for_execution`)
- System message enrichment with Zep memory context
- Thread-based conversation memory storage
- User and named knowledge graph support
- Typed parameters with `Annotated` for AG2 tool descriptions
- Single shared background event loop and reused Zep client for sync bridging
  (Python 3.11–3.13 compatible)
- Message (4000 char) and graph-data (9900 char) size guards with truncation

[0.1.0]: https://github.com/getzep/zep/releases/tag/zep-ag2-v0.1.0
