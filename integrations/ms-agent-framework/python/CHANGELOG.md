# Changelog

## 0.2.0 (2026-07-07)

### Added

- `ensure_user` / `ensure_thread` -- idempotent, out-of-band helpers (new `zep_ms_agent_framework.provisioning` module) to provision the Zep user and thread before the first run. Both return whether the resource was newly created. `ensure_user` accepts an optional `on_created` hook (`UserSetupHook`) that fires only when the user is genuinely new; a genuine failure (auth, network, 5xx) or an `on_created` hook error always propagates. `ZepContextProvider._ensure_resources` now calls these helpers on its lazy hot path, which keeps its existing "log + degrade to no-memory, never raise" contract -- so a hook failure there is logged and skips that turn's persistence, while a direct out-of-band `ensure_user` call lets the same hook failure propagate to the caller.
- `context_builder` constructor kwarg on `ZepContextProvider` -- an optional async callable that replaces the default `thread.add_messages(return_context=True)` context retrieval with custom logic (e.g. a filtered graph search, a different graph entirely). Receives a single `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`, `session_context`). When set, message persistence and context building run **concurrently** in `before_run`, with per-side failure isolation: a builder error is logged and skips injection but does not stop persistence; a persistence error is logged (turn not marked as persisted, eligible for retry) but a successful builder result is still injected.
- `context_template` constructor kwarg on `ZepContextProvider` -- configures the template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block, canonical across zep-adk's Python, Go, and TypeScript implementations). Rendered via plain string replacement, never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject. See "Changed" below for the resulting wording change.
- `create_zep_search_tool` (new `zep_ms_agent_framework.search` module) -- a factory producing a model-callable `agent_framework.FunctionTool` over `graph.search`, with pin-or-expose control over which search parameters the model can set (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`, all model-exposed by default). `search_pinned_params` fixes a parameter to a value and hides it from the model; `search_hidden_params` hides a parameter without pinning it (Zep's own default applies). `search_filters` and `bfs_origin_node_uuids` remain constructor-only.
- `expose_search_tool` constructor kwarg on `ZepContextProvider` (default `False`) -- when `True`, `before_run` registers the graph-search tool via `context.extend_tools(self.source_id, [tool])` so the model can search the knowledge graph on demand, in addition to the context injected automatically every turn. `search_pinned_params`, `search_hidden_params`, `search_filters`, and `bfs_origin_node_uuids` constructor kwargs configure the exposed tool.
- Live integration test `test_ensure_helpers_and_before_after_run`, gated on `ZEP_API_KEY` alone (no OpenAI call needed) -- provisions a user/thread via `ensure_user`/`ensure_thread` and drives a `before_run`/`after_run` cycle against real Zep with a fake `SessionContext` double, asserting no exceptions and that both turns round-trip through `thread.get`.

### Changed

- **Breaking: the default injected context wording changed.** The hardcoded pre-0.2.0 wrapper -- `"The following context is retrieved from Zep's long-term memory service. It contains relevant facts, relationships, and prior knowledge about the user. Use it to inform your responses.\n\n<ZEP_CONTEXT>\n{context_block}\n</ZEP_CONTEXT>"` -- is replaced by `DEFAULT_CONTEXT_TEMPLATE` (`"The following context is retrieved from Zep, the agent's long-term memory. It contains relevant facts, entities, and prior knowledge about the user. Use it to inform your responses.\n\n<ZEP_CONTEXT>\n{context}\n</ZEP_CONTEXT>"`), now overridable via `context_template`. If you depend on the exact old wording (e.g. in a prompt-matching test), pass it explicitly via `context_template=...`.
- **`on_user_created` semantics clarified and tightened.** The hook is now passed through to `provisioning.ensure_user` as its `on_created` hook. On the provider's lazy hot path, a hook failure is treated the same as a genuine provisioning failure: it is logged and swallowed (never raised into `before_run`), and this turn's Zep persistence is skipped -- previously, a hook failure was caught internally and did *not* block that turn's message persistence. Calling `ensure_user` directly, out-of-band, surfaces a hook failure to the caller (it always propagates there, by contract).

### Investigated

- **Per-run identity (dimension H).** The installed `agent_framework` exposes no per-run user/session identity: `SupportsAgentRun.run()`, `AgentSession`, and `SessionContext` carry no `user_id`-shaped field, and there is no framework convention (documented or in source) for stashing identity in `session.state`, unlike e.g. Google ADK's `tool_context.state["zep_user_id"]` pattern. The `state` dict actually passed to `before_run`/`after_run` is additionally scoped per-provider (`session.state.setdefault(source_id, {})`), not the full session state. No code change was made; `ZepContextProvider` continues to bind `user_id`/`thread_id` at construction, one provider (and typically one agent) per user/conversation. Documented in the class docstring.

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
