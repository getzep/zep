# Changelog

All notable changes to the zep-crewai package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0]

### Breaking

Follow the Zep v4 migration guide for the general migration procedure — the
nullable UUID columns, the one-time `lookup` backfill, and the order of
steps: https://help.getzep.com/migrating-from-v3

The API changes that are specific to this package are:

- The package requires the Zep v4 SDK (`zep-cloud>=4.0.0,<5`). It targets the
  v4 API only and does not support v3.
- `ZepUserStorage` and `ZepStorage` take `user_uuid` and `thread_uuid` (with
  an optional `graph_uuid`) instead of `user_id` and `thread_id`.
- `ZepGraphStorage`, `create_search_tool`, `ZepSearchTool`, and
  `create_add_data_tool` take `graph_uuid` instead of `graph_id` or `user_id`.
- `ContextInput` carries `user_uuid`, `thread_uuid`, `graph_uuid`, and
  `user_message` instead of `user_id` and `thread_id`.
- `ensure_user` and `ensure_thread` take no `user_id` or `thread_id`:
  `ensure_thread` takes `user_uuid`, and both helpers return the new resource
  together with a creation flag. A failed create, including a conflict, raises
  the error.
- The `on_created` hook signature is now `Callable[[Zep, User], None]`. The
  hook receives the created `User`, which carries `uuid_` and `graph_uuid`.
- `ZepUserStorage` and `ZepGraphStorage` replace `facts_limit` and
  `entity_limit` with `max_characters`. Zep v4 controls the size of the Context
  Block on the server, so the two client-side counts have no v4 equivalent.
- Removed lazy provisioning from `ZepUserStorage` and `ZepStorage`, together
  with their `first_name`, `last_name`, `email`, and `on_created` constructor
  arguments. A UUID identifies a resource that already exists; use
  `ensure_user` and `ensure_thread` one time, during onboarding.

For an existing V3 user, thread, or graph, do not call `ensure_user`,
`ensure_thread`, or `client.graph.create`. Get its UUID one time with the
backfill in the migration guide, and store it.

### Changed

- Search calls use the stable v4 `page_size` parameter. The tool-facing
  `limit` parameter is unchanged: the package translates it to `page_size`
  on the SDK call.
- Message and graph writes use the v4 methods `thread.add_messages(thread_uuid,
  ...)` and `graph.episode.add(graph_uuid, ...)`.
- Thread context uses `thread.get_context(thread_uuid)`.
- Graph context uses `graph.get_context(graph_uuid, ...)`, which Zep assembles
  on the server. The v3 client-side helper `compose_context_string` is not in
  the v4 SDK.
- The search tool calls one v4 method for each scope: `graph.search_edges`,
  `graph.search_nodes`, `graph.search_episodes`, `graph.search_observations`,
  and `graph.search_thread_summaries`. Scope `auto` calls `graph.get_context`.
  Each search method returns a pager, which the tool reads up to `limit`.
- Scope `auto` takes no `reranker`, and the tool no longer drops one
  silently. A model-supplied reranker with `auto` returns an error that asks
  the model to search again with no reranker or with another scope, without
  calling Zep. A pinned reranker with `scope` pinned to `auto` raises
  `ValueError` at construction. The model-facing `reranker` parameter has no
  schema default: a model that omits it gets `rrf` on every scope except
  `auto`. When a reranker is pinned, the scope field does not offer `auto`.

## [1.2.2] - 2026-10-01

### Changed

- The package now requires `zep-cloud>=3.23.0,<4`. The upper bound keeps the package on the v3 SDK when `zep-cloud` 4.x is published.

## [1.2.1] - 2026-07-29

### Added

- Added the `py.typed` marker so installed `zep-crewai` packages expose their inline type annotations to PEP 561-compatible type checkers.

## [1.2.0]

CrewAI 1.x removed its memory extension points (`crewai.memory.storage.interface.Storage`
and the `ExternalMemory(storage=...)` wrapper), so there is **no automatic per-turn memory
loop to build** -- the supported extension points remain tools, the standalone storage
adapters called from your app code, and kickoff-level seeding (re-check on future CrewAI
releases). Within that ceiling, this release brings `zep-crewai` up to the standardization
bar set by the other Zep framework integrations: out-of-band provisioning with lazy
fallback, a pluggable context builder, a configurable context template, a pin-or-expose
tool schema, payload truncation guards, and a `save()` that never raises into the crew.
This package is sync-only (CrewAI's adapters are built on the synchronous `Zep` client),
so all new APIs are synchronous and use the canonical names without a `_sync` suffix.

### Added

- `ensure_user` / `ensure_thread` -- idempotent, out-of-band helpers (new
  `zep_crewai.provisioning` module) to provision the Zep user and thread before the first
  turn. Both return whether the resource was newly created, so callers can drive one-time
  per-user setup. `ensure_user` accepts an optional `on_created` hook (`UserSetupHook`,
  sync: `Callable[[Zep, str], None]`) that fires only when the user is genuinely new; a
  genuine failure (auth, network, 5xx) or an `on_created` hook error always propagates
  when called directly.
- `ZepUserStorage` and `ZepStorage` now lazily provision the Zep user and thread on first
  `save()`/`search()` (previously both had to exist before the first call, or `save()`
  failed). The lazy path is hot-path-wrapped: a genuine failure or an `on_created` hook
  error is logged and returns `False`, never raised into `save()`/`search()`. New
  constructor kwargs `first_name`, `last_name`, `email`, and `on_created` feed this path.
  `ZepGraphStorage` deliberately has **no** `on_created` (it is scoped to a standalone
  `graph_id`, not a Zep user -- there is no "user created" event to hook into) and now
  raises `TypeError` if one is passed.
- `context_builder` constructor kwarg on `ZepUserStorage` (+ `ContextInput`,
  `ContextBuilder` exports) -- an optional **sync** callable that entirely replaces the
  default thread-context + graph composition in `search()`. Receives a single frozen
  `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`); returns the context
  string or `None` for "no results". A builder exception is logged and degrades to empty
  results (the existing `search()` error contract). There is no concurrency here --
  persistence (`save`) is a separate, caller-driven call in CrewAI's model, so nothing is
  gathered against the builder.
- `context_template` constructor kwarg on `ZepUserStorage` and `ZepGraphStorage` (and a
  `context_template` parameter on `search_graph_and_compose_context`) -- configures the
  template wrapping the composed/built context returned from `search()` (default:
  `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block,
  canonical across zep-adk's Python, Go, and TypeScript implementations). Rendered via
  plain string replacement (`str.replace("{context}", ...)`), never `str.format`, so
  context text containing `{`, `}`, or `%` is always safe to inject.
- `ZepSearchTool` / `create_search_tool` gain pin-or-expose control: `scope` (all six of
  `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto`), `reranker`
  (`rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`), `limit`,
  `mmr_lambda`, and `center_node_uuid` are now all model-exposed by default. New
  `pinned_params`/`hidden_params` keyword arguments fix a parameter to a constant (hidden
  from the model) or hide it without pinning (Zep's own default applies). `search_filters`
  and `bfs_origin_node_uuids` are new constructor-only keyword arguments (never exposed
  to the model).
- New `zep_crewai.limits` module: `truncate_message_content` guards the
  `save()`-to-`thread.add_messages` paths (`ZepStorage`, `ZepUserStorage`) against Zep's
  4,096-char message limit (truncates to 4,000, logging lengths only, never content);
  `truncate_graph_data` guards the `graph.add` paths (`ZepGraphStorage.save`,
  `ZepAddDataTool`, and the storage adapters' graph save paths) against Zep's payload
  ceiling (truncates to 9,900 chars, matching the ag2/autogen precedent).

### Changed

- **Breaking: `ZepSearchTool` / `create_search_tool`'s tool schema changed
  (pin-or-expose).** Previously the model saw `query`, `limit`, and a freeform `scope`
  string with four documented values (`edges`, `nodes`, `episodes`, `all`); everything
  else was hardcoded. Now the `args_schema` is built dynamically with
  `pydantic.create_model` and exposes `scope` (six typed values -- the compound `all`
  scope is removed; pin or let the model choose a scope, or use `auto` to let Zep
  decide), `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid`. New
  `scope=`/`reranker=`/`limit=` constructor arguments pin (and hide) the corresponding
  parameter for fixed configuration. A parameter neither pinned nor supplied by the
  model (e.g. `mmr_lambda` left unset) is omitted from the `graph.search` call entirely,
  never forwarded as an explicit `None`. Result formatting changed from the numbered
  `[FACT]`/`[ENTITY]` list to the compact `- fact` line format shared by the sibling
  integrations, and a Zep failure returns an error string rather than raising.
- **Breaking (behavioral): `save()` no longer raises on Zep errors.** Previously
  `ZepStorage.save()`, `ZepUserStorage.save()`, and `ZepGraphStorage.save()` logged the
  error and re-raised it into the crew, crashing the run on a Zep outage. All three now
  log the error and return normally -- persistence failures degrade gracefully instead of
  propagating. Callers that relied on catching those exceptions should use
  `ensure_user`/`ensure_thread` out-of-band for loud provisioning failures and monitor
  logs for persistence errors.
- `search()` results from `ZepUserStorage`/`ZepGraphStorage` are now wrapped in
  `context_template` (previously the raw `compose_context_string` output was returned).
  Callers that parsed the raw composition should read the block inside
  `<ZEP_CONTEXT>...</ZEP_CONTEXT>` or pass `context_template="{context}"` to restore the
  old shape.

### Migrating from 1.1.x

**Pinning search parameters:**

```python
# Before (1.1.x) -- the model saw query/limit/scope, everything else was hardcoded
tool = create_search_tool(zep_client, user_id="user-1")

# After (1.2.0) -- all search params are model-exposed by default; pin what the
# model should not control:
tool = create_search_tool(
    zep_client, user_id="user-1", pinned_params={"scope": "nodes", "limit": 5}
)
# or hide a param without pinning it (Zep's own default applies):
tool = create_search_tool(zep_client, user_id="user-1", hidden_params={"reranker"})
```

**Provisioning a user before the first turn (optional -- the storage adapters now also
provision lazily, but out-of-band provisioning fails loudly and is recommended):**

```python
from zep_crewai import ensure_user, ensure_thread

ensure_user(zep_client, user_id="user-1", first_name="Jane", email="jane@example.com")
ensure_thread(zep_client, thread_id="thread-1", user_id="user-1")

storage = ZepUserStorage(client=zep_client, user_id="user-1", thread_id="thread-1")
```

## [1.1.2] - 2026-06-16

### Changed
- Modernized for the latest dependencies: CrewAI 1.x and `zep-cloud>=3.23.0`.
- `ZepStorage`, `ZepUserStorage`, and `ZepGraphStorage` are now standalone,
  framework-agnostic Zep storage adapters. CrewAI 1.x removed
  `crewai.memory.storage.interface.Storage` (and the `ExternalMemory(storage=...)`
  wrapper / `external_memory=` Crew kwarg that consumed it), so these classes no
  longer subclass a CrewAI base. Their public `save(value, metadata)` /
  `search(query, limit, score_threshold)` / `reset()` API and Zep behavior
  (messages → `thread.add_messages`, data → `graph.add`, search →
  `thread.get_user_context` + `graph.search`) are preserved.
- Dependency-check import in `__init__.py` switched from the removed
  `crewai.memory.storage.interface` to `crewai.tools` (the supported extension
  point used by `ZepSearchTool` / `ZepAddDataTool`).
- Updated examples and README to wire Zep into CrewAI agents via the
  `ZepSearchTool` / `ZepAddDataTool` instead of the removed `ExternalMemory`.

### Removed
- Dropped the `mode` argument from `thread.get_user_context` calls. Zep V3 removed
  the thread context `mode` ("summary"/"basic") option and auto-assembles the
  Context Block.

### Deprecated
- `ZepUserStorage(mode=...)` is now accepted for backward compatibility but ignored,
  and emits a `DeprecationWarning`.

### Dependencies
- `zep-cloud` lower bound raised to `>=3.23.0`.
- `crewai` lower bound raised to `>=1.0.0`.
