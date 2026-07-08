# Changelog

## 0.2.0 (2026-07-07)

### Added

- `ensure_user` / `ensure_thread` -- idempotent, out-of-band helpers (in the new `zep_pydantic_ai.provisioning` module) to provision the Zep user and thread before the first turn. Both return whether the resource was newly created, so callers can drive one-time per-user setup. `ensure_user` accepts an optional `on_created` hook (`UserSetupHook`) that fires only when the user is genuinely new; a genuine failure (auth, network, 5xx) or an `on_created` hook error always propagates. `ZepDeps`'s lazy `ensure_user_and_thread()` call on the history-processor hot path keeps its existing "log + degrade to no-memory, never raise" contract and now accepts the same `on_created` hook.
- `context_builder` field on `ZepDeps` -- an optional async callable that replaces the default `thread.add_messages(return_context=True)` context retrieval with custom logic (e.g. a filtered graph search, a different graph entirely). Receives a single `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`, `run_context`). When set, message persistence and context building run **concurrently**, with per-side failure isolation: a builder error is logged and skips injection but does not stop persistence; a persistence error is logged (turn not marked as persisted, eligible for retry) but a successful builder result is still injected.
- `context_template` field on `ZepDeps` -- configures the template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block). Rendered via plain string replacement, never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject. Canonical across zep-adk's Python, Go, and TypeScript implementations.
- `observations` and `thread_summaries` scopes, `mmr_lambda`, and `center_node_uuid` added to `create_zep_search_tool`'s exposed search parameters, alongside `scope`, `reranker`, and `limit` -- all five now default to model-exposed with documented defaults.
- `pinned_params` and `hidden_params` constructor arguments on `create_zep_search_tool` -- pin any search parameter to a fixed value (hidden from the model, always sent) or hide it from the model schema without pinning (Zep's own default applies). `search_filters` and `bfs_origin_node_uuids` remain constructor-only (never exposed to the model).
- `zep_capabilities(deps)` and `create_zep_after_run_hook(deps)` (new `zep_pydantic_ai.capabilities` module) -- automatic assistant-message persistence via Pydantic AI's `Hooks(after_run=...)` capability. `zep_capabilities(deps)` returns `[ProcessHistory(zep_history_processor), Hooks(after_run=...)]` for one-line wiring; no explicit `persist_run` call is needed after `agent.run`. `persist_run` remains exported for callers who want explicit control.

### Changed

- **Breaking: `create_zep_search_tool` now returns a `pydantic_ai.Tool` instead of a bare async function.** The returned `Tool` still works as a drop-in element of `tools=[...]`; only code that called the previous return value directly as a function (e.g. in tests) needs to change, to `tool.function(ctx, query=..., **kwargs)`. This was required to hand-craft the JSON schema per pin-or-expose configuration rather than introspecting a fixed function signature. See "Migrating from 0.1.x" below.
- **Breaking: `create_zep_search_tool`'s `scope`, `reranker`, and `limit` constructor arguments now pin (and hide) those parameters** instead of just setting a call-time default the model could not see or override in the schema -- they were never model-visible before, so existing callers see no behavior change, but new code should prefer `pinned_params={"scope": ..., "reranker": ..., "limit": ...}` for the equivalent effect alongside the new pinnable parameters.
- `make_context_request` now renders via `template.replace("{context}", context)` against a caller-supplied `template` (defaulting to `DEFAULT_CONTEXT_TEMPLATE`) instead of a hardcoded f-string. The default wording changed slightly to match the canonical cross-language template; the `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper is unchanged.

### Migrating from 0.1.x

**Search tool used as a bare callable (tests, custom wiring):**

```python
# Before (0.1.x)
tool = create_zep_search_tool()
result = await tool(ctx, "query text")

# After (0.2.0)
tool = create_zep_search_tool()
result = await tool.function(ctx, query="query text")
```

**Pinning search parameters:**

```python
# Before (0.1.x) -- scope/reranker/limit set a default; the model never saw them
tool = create_zep_search_tool(scope="nodes", limit=5)

# After (0.2.0) -- back-compat aliases still work unchanged, or be explicit:
tool = create_zep_search_tool(scope="nodes", limit=5)  # still works
tool = create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5})  # equivalent
```

**Automatic assistant persistence (optional, opt-in):**

```python
# Before (0.1.x) -- explicit persist_run after every run
result = await agent.run("Hi", deps=deps)
await persist_run(deps, result.new_messages())

# After (0.2.0) -- opt into automatic persistence via zep_capabilities
from zep_pydantic_ai import zep_capabilities

agent = Agent(..., deps_type=ZepDeps, capabilities=zep_capabilities(deps))
result = await agent.run("Hi", deps=deps)  # assistant reply already persisted
```

## 0.1.0 (2026-06-16)

### Added

- `ZepDeps` -- dataclass carrying the Zep client and user/thread identity, used as the agent's `deps_type`.
- `zep_history_processor` -- a Pydantic AI history processor (registered via `capabilities=[ProcessHistory(...)]`) that persists each user turn via `thread.add_messages(return_context=True)` and prepends Zep's context block to the prompt. Dedupes by latest user message to handle `ProcessHistory`'s once-per-model-request invocation, preventing duplicate episodes during tool-calling runs.
- `persist_run` -- helper to persist the assistant reply from `result.new_messages()` to the Zep thread, skipping tool-call scaffolding.
- `create_zep_search_tool` -- factory producing a model-callable `@agent.tool` over `graph.search`, targeting the current user's graph or a standalone graph.
- Lazy Zep user and thread creation on first use.
- Graceful error handling throughout: Zep failures are logged and never crash the agent run; failed persists are retried.
- Mock-based test suite plus end-to-end wiring tests using Pydantic AI's `TestModel`.
- Working example demonstrating fact seeding and memory recall.
