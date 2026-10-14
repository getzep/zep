# Changelog

## 1.0.0

### Breaking

- **The package targets the Zep v4 SDK (`zep-cloud>=4.0.0,<5`) only.** There is no compatibility layer for v3.

To migrate from V3, follow the [V3 to V4 migration guide](https://help.getzep.com/migrating-from-v3) for the general procedure: the nullable UUID columns, the one-time `lookup` backfill, and the order of the steps.

These API changes are specific to this package:

- `get_zep_context`, `get_zep_context_sync`, `build_system_message`, and `build_system_message_sync`: `thread_id` becomes `thread_uuid` (give the stored `Thread.uuid_`), `user_id` becomes `graph_uuid` (give the stored `User.graph_uuid`), and `template_id` becomes `template_uuid`.
- `create_zep_pre_model_hook`: `thread_id` becomes `thread_uuid`, `user_id` becomes `graph_uuid`, and `template_id` becomes `template_uuid`.
- `ContextInput`: the `user_id` and `thread_id` fields become `graph_uuid` and `thread_uuid`.
- `persist_messages` and `persist_messages_sync`: `thread_id` becomes `thread_uuid` (give the stored `Thread.uuid_`).
- `create_graph_search_tool` and `create_graph_search_tool_sync`: the `user_id` and `graph_id` arguments become one required `graph_uuid` argument. Give the stored `User.graph_uuid` for a user graph, or the stored `Graph.uuid_` for a standalone graph.
- `ZepStore`: the constructor requires `graph_uuid` (the stored `User.graph_uuid` or `Graph.uuid_`). A `namespace_target` resolver returns a graph UUID string, not a `{"user_id": ...}` or `{"graph_id": ...}` dict. The default resolver no longer uses the first namespace element as a graph ID.
- `ensure_user`, `ensure_thread`, `ensure_user_sync`, and `ensure_thread_sync` are removed. Use `create_user`, `create_thread`, `create_user_sync`, and `create_thread_sync` for new resources only. `create_user` takes no `user_id` and returns the created `User` (store `User.uuid_` and `User.graph_uuid`). `create_thread` takes `user_uuid` in place of `thread_id` and `user_id`, and returns the created `Thread` (store `Thread.uuid_`). The `on_created` hook receives the created `User` in place of a user ID.
- `to_zep_message` and `to_zep_messages` return the v4 `AddMessage` type in place of the v3 `Message` type.

For an existing V3 user, thread, or graph, do not call `create_user`, `create_thread`, or `graph.create`. Get its UUID one time with the backfill in the migration guide, and store it.

### Changed

- Context retrieval uses `thread.get_context(thread_uuid)` in place of the v3 `thread.get_user_context(thread_id)`.
- Persistence uses the v4 `AddMessage` type and `thread.add_messages(thread_uuid, ...)`.
- `create_graph_search_tool` and `ZepStore` use the dedicated v4 search methods in place of the v3 aggregate `graph.search`. The `scope` selects the method: `edges`, `nodes`, `episodes`, `observations`, and `thread_summaries` call `graph.search_edges`, `graph.search_nodes`, `graph.search_episodes`, `graph.search_observations`, and `graph.search_thread_summaries`; `auto` calls `graph.get_context`, which accepts neither a `reranker` nor a `limit`. Results are read from the first page of a pager (`page.items`). The tool `limit` parameter and the `ZepStore` search limit are sent as the v4 `page_size` argument. The constructor-only `search_filters` argument is sent as the v4 `filters` argument, and its type must be the filter type of the selected scope, for example `EdgeSearchFilters` for `edges`. The search tool omits a model-chosen reranker that the selected scope does not support, for example `cross_encoder` on `episodes`, because Zep rejects that request with HTTP 400. The tool schema shows only the rerankers that the model can use correctly for the scope. `node_distance` is shown only when `center_node_uuid` is pinned, and `episode_mentions` only when `search_filters` sets `episode_uuids`. When the reranker is `mmr` and no `mmr_lambda` is set, the tool sends `DEFAULT_MMR_LAMBDA` (0.5). An argument outside the schema, or `node_distance` without `center_node_uuid`, gives the model an error message that names the setting and the valid values. A pinned scope and reranker pair that Zep rejects, including any pinned reranker with `scope="auto"`, raises `ValueError` at construction.
- `ZepStore` ingestion uses `graph.episode.add(graph_uuid, data=..., type="json")` in place of the v3 `graph.add`. `ZepStore` now requires a `graph_uuid`, and `namespace_target` maps a namespace to the UUID of a graph instead of to a v3 target.

## 0.2.1 (2026-07-29)

### Added

- Added the `py.typed` marker so installed `zep-langgraph` packages expose their inline type annotations to PEP 561-compatible type checkers.

## 0.2.0 (2026-07-07)

### Added

- `ensure_user` / `ensure_thread` (+ synchronous `ensure_user_sync` / `ensure_thread_sync`) -- idempotent, out-of-band helpers (new `zep_langgraph.provisioning` module) to provision the Zep user and thread before the first turn. Both return whether the resource was newly created, so callers can drive one-time per-user setup. `ensure_user`/`ensure_user_sync` accept an optional `on_created`/`UserSetupHook`(`Sync`) hook that fires only when the user is genuinely new; a genuine failure (auth, network, 5xx) or an `on_created` hook error always propagates. These are plain module-level functions with no instance caching -- callers that want to avoid redundant calls should cache the "already provisioned" result themselves.
- `context_builder` keyword argument (+ `ContextInput`, `ContextBuilder`, `ContextBuilderSync`) on `get_zep_context` / `get_zep_context_sync` / `build_system_message` / `build_system_message_sync` -- an optional callable that *replaces* the default `thread.get_user_context` retrieval with custom logic (e.g. a filtered graph search, a different graph entirely). Receives a single `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`) -- these are plain functions with no surrounding framework object, so `ContextInput` carries only the Zep call inputs. New `user_id`/`user_message` keyword arguments populate it. Because there is no single caller-owned "turn" object for these node helpers to hook into, persistence and context building are not run concurrently by the helpers themselves; callers who want that parallelism gather `persist_messages` and `get_zep_context`/`build_system_message` themselves (see the README snippet).
- `create_zep_pre_model_hook` (new `zep_langgraph.hooks` module) -- a prebuilt `pre_model_hook` for `langgraph.prebuilt.create_react_agent` that injects Zep context on every model call via the hook's documented `llm_input_messages` return key (verified against the installed `langgraph==1.2.8`'s `create_react_agent`/`chat_agent_executor` contract), so injected context never becomes part of persisted thread history. Supports `context_builder`, `template`, `template_id`, and `base_instructions`, using the same retrieval path as `build_system_message`. Persistence remains via `persist_messages`, called separately after the model responds.
- `DEFAULT_CONTEXT_TEMPLATE` is now **canonical** across zep-adk's Python, Go, and TypeScript implementations (an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block) -- see "Breaking" below.
- `observations` and `thread_summaries` scopes, `mmr_lambda`, and `center_node_uuid` added to `create_graph_search_tool`'s exposed search parameters, alongside `scope`, `reranker`, and `limit` -- all five now default to model-exposed with documented defaults. New `bfs_origin_node_uuids` constructor-only argument for BFS seeding.
- `pinned_params` and `hidden_params` constructor arguments on `create_graph_search_tool` / `create_graph_search_tool_sync` -- pin any search parameter to a fixed value (hidden from the model, always sent) or hide it from the model schema without pinning (Zep's own default applies).

### Changed

- **Breaking: `create_graph_search_tool` / `create_graph_search_tool_sync`'s tool schema changed (pin-or-expose).** Previously only `query` was exposed to the model; `scope`, `reranker`, and `limit` were fixed at construction with no model visibility. Now `scope` (all six of `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto`), `reranker` (`rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`), `limit`, `mmr_lambda`, and `center_node_uuid` are all model-exposed by default, built dynamically via `pydantic.create_model` and passed as the `StructuredTool`'s `args_schema`. Existing `create_graph_search_tool(client, user_id=..., scope=..., limit=...)` calls still work: the `scope`/`reranker`/`limit` constructor arguments now pin (and hide) the corresponding parameter instead of just setting a fixed value the model never saw -- equivalent runtime behavior, but new code should prefer `pinned_params={"scope": ..., "limit": ...}` alongside the newly pinnable parameters. A param neither pinned nor supplied by the model (e.g. `mmr_lambda` left unset) is omitted from the `graph.search` call entirely, never forwarded as an explicit `None`.
- **Breaking: `DEFAULT_CONTEXT_TEMPLATE` wording changed** from the `<MEMORY>...</MEMORY>` wrapper to the canonical `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block shared across zep-adk's Python, Go, and TypeScript implementations. Callers who rely on the exact default wording (e.g. snapshot tests) should update; the `template`/`template_id` parameters are unchanged, so a custom template continues to work as before.
- `format_context_block` now renders via `template.replace("{context}", context)` instead of `template.format(context=...)`. A custom `template` containing literal `{`, `}`, or `%` outside of the `{context}` placeholder is now handled safely; `str.format` would previously have raised or mangled such templates.

### Migrating from 0.1.x

**Pinning search parameters:**

```python
# Before (0.1.x) -- scope/reranker/limit were constructor-only, no model visibility
tool = create_graph_search_tool(zep, user_id="user-1", scope="nodes", limit=5)

# After (0.2.0) -- legacy kwargs still work as pins, or be explicit:
tool = create_graph_search_tool(zep, user_id="user-1", scope="nodes", limit=5)  # still works
tool = create_graph_search_tool(
    zep, user_id="user-1", pinned_params={"scope": "nodes", "limit": 5}
)  # equivalent
```

**Provisioning a user/thread before the first turn (new, optional):**

```python
from zep_langgraph import ensure_thread, ensure_user

await ensure_user(zep, user_id="user-1", first_name="Jane", email="jane@example.com")
await ensure_thread(zep, thread_id="thread-1", user_id="user-1")
```

**Custom context template (if pinned to the old default wording):**

```python
# Before (0.1.x default)
"<MEMORY>\n{context}\n</MEMORY>"

# After (0.2.0 default) -- pass the old wording explicitly to keep it unchanged:
await build_system_message(zep, thread_id, template="<MEMORY>\n{context}\n</MEMORY>")
```

## 0.1.0 (2026-06-16)

### Added

- **Node / tool helpers (primary path):**
  - `get_zep_context` / `get_zep_context_sync` — fetch a thread's Context Block
    via `thread.get_user_context`.
  - `build_system_message` / `build_system_message_sync` — fold the Context Block
    and base instructions into a LangChain `SystemMessage` for prompt injection.
  - `format_context_block` — combine base instructions with a Context Block.
  - `persist_messages` / `persist_messages_sync` — persist a conversation turn via
    `thread.add_messages`; accept LangChain or Zep messages, with optional
    `return_context` to fold persist + retrieve into one round-trip.
  - `to_zep_message` / `to_zep_messages` — convert LangChain messages to Zep
    messages (role mapping, multimodal-content flattening, length truncation).
  - `create_graph_search_tool` / `create_graph_search_tool_sync` — prebuilt
    LangChain `StructuredTool` over `graph.search`, ready for `create_react_agent`.
- **`ZepStore` (secondary path):** a hybrid-delegate
  `langgraph.store.base.BaseStore`. Implements only the two abstract methods
  (`batch` / `abatch`); delegates exact-key `get` / `put` / `delete` /
  `list_namespaces` to a backing KV store (default `InMemoryStore`), ingests every
  `put` into Zep (`graph.add` with `type="json"`), and routes `search` to Zep
  semantic `graph.search`. Configurable namespace→target resolver, search scope,
  ingestion toggle, and backing-store search merge.
- Graceful error handling throughout: a Zep failure is logged and never crashes
  the host agent.
- Mock-based test suite, two runnable examples (`react_agent.py`,
  `store_agent.py`), README, and SETUP guide.

### Notes

- Targets the Zep V3 SDK (`zep-cloud>=3.23.0`) and `langgraph>=1.2.5`.
- Zep ingestion is asynchronous — there is no read-after-write of graph facts
  within a turn.
