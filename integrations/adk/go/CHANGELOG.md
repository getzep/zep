# Changelog

## 0.2.0 (2026-07-06)

### Added

- `NewGraphSearchTool` now builds an explicit JSON schema (via
  `github.com/google/jsonschema-go`) giving every model-exposable search
  parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) a
  pin-or-expose tri-state, matching the Python and TypeScript zep-adk
  packages: **exposed** (default; the model chooses, with a documented
  default), **pinned** (hidden from the schema, always sent to Zep with a
  fixed value), or **hidden** (omitted from both the schema and the Zep call).
  New options: `WithToolReranker`, `WithToolMMRLambda`,
  `WithToolCenterNodeUUID` (pin-and-hide), `WithToolSearchFilters`,
  `WithToolBFSOriginNodeUUIDs` (constructor-only, always applied), and
  `WithHiddenParams` (hide without pinning). See the README's "Graph search
  tool: pin-or-expose parameters" section.
- `thread_summaries` added to the set of Zep graph search scopes the memory
  service and search tool map into results (`name: summary`, mirroring the
  `nodes`/`observations` shape). The model-exposed `scope` enum now covers all
  six Zep scopes: `edges`, `nodes`, `episodes`, `observations`,
  `thread_summaries`, `auto`.
- `WithContextBuilder` — configure a custom `ContextBuilder` function to
  construct the context block injected by `NewBeforeModelCallback`, instead of
  relying on `Thread.AddMessages(ReturnContext=true)`. When set, message
  persistence and the builder run concurrently, each isolated from the
  other's failure: a builder error only skips injection for that turn
  (persistence still completes); a persist failure does not prevent a
  successful builder result from being injected.
- `WithContextTemplate` / `DefaultContextTemplate` — configure the template
  used to wrap the injected Zep context block. Rendered via
  `strings.ReplaceAll` (never `fmt` verbs), so context content containing
  `%`, `{`, or `}` is always safe to inject. `DefaultContextTemplate` is
  canonical across zep-adk's Python, Go, and TypeScript implementations.
- `ContextInput` — the struct passed to a custom `ContextBuilder`, carrying
  the concrete Zep client, resolved user/thread IDs, the user's message text,
  the ADK callback context, and the outgoing `*model.LLMRequest`.

### Changed

- **Breaking:** `WithToolSearchScope` and `WithToolSearchLimit` now **pin and
  hide** the scope/limit parameter (fixed value, removed from the model's tool
  schema) instead of silently configuring a hidden default that every search
  used. An *absent* option now **exposes** that parameter to the model (with
  the documented default: `scope="edges"`, `limit=10`) instead of pinning it —
  previously the model never saw or controlled these parameters at all.
  Callers who want the old always-pinned-to-a-fixed-value behavior should pass
  `WithToolSearchScope` / `WithToolSearchLimit` explicitly (as before) and add
  `WithHiddenParams` for the newly-exposed `reranker`, `mmr_lambda`, and
  `center_node_uuid` parameters if they should stay hidden too. See the
  README's "Graph search tool: pin-or-expose parameters" migration recipe.
- **Breaking:** The default context block injected by `NewBeforeModelCallback`
  is now wrapped in `DefaultContextTemplate` (an explicit `<ZEP_CONTEXT>...
  </ZEP_CONTEXT>` block with framing text) instead of the bare
  `DefaultContextPrefix` string. Callers relying on the old bare-prefix output
  should switch to `WithContextTemplate` (or the deprecated `WithContextPrefix`
  shim, which reproduces the previous prefix-only formatting).
- `WithContextPrefix` is now **deprecated** in favor of `WithContextTemplate`.
  It remains fully functional as a shim: it sets the template to
  `prefix + "{context}"`, reproducing the old prefix-only output exactly (no
  `<ZEP_CONTEXT>` wrapper). `DefaultContextPrefix` remains exported for
  existing callers but is no longer used by the default template.
- **Breaking:** `EnsureUser` and `EnsureThread` now return `(created bool, err
  error)` instead of `error`, so callers can tell whether the user/thread was
  newly created and drive one-time per-user setup (ontology, custom
  instructions) reliably. `created` is `true` only when the resource was
  newly created by that call; an already-exists conflict returns `(false,
  nil)` (not an error), and a nil client returns `(false, nil)` as before.

  Migration:

  ```go
  // Before: if err := zepadk.EnsureUser(ctx, zep, userID, first, last, email); err != nil { ... }
  // After:
  created, err := zepadk.EnsureUser(ctx, zep, userID, first, last, email)
  if err != nil {
      // handle genuine failure
  }
  if created {
      // one-time per-user setup: ontology, custom instructions, etc.
  }
  ```

  `EnsureThread` follows the same pattern.

## 0.1.0 (2026-06-16)

### Added

- `NewBeforeModelCallback` — an ADK `llmagent.BeforeModelCallback` that persists
  each new user turn to a Zep thread and injects the returned Context Block into
  the model request's system instruction (`Thread.AddMessages` with
  `ReturnContext=true`). It detects tool-loop continuations (a function response
  as the latest content in `req.Contents`) and skips re-persisting/re-injecting,
  so a turn that calls `search_memory` is recorded exactly once. Oversize
  messages are truncated to Zep's 4,096-character limit with a lengths-only
  warning (content is never dropped or logged). Configurable via
  `WithContextPrefix`, `WithUserMessageName`, and `WithLogger`.
- `NewAfterModelCallback` — an ADK `llmagent.AfterModelCallback` that persists the
  assistant's text reply back to the same Zep thread, so the user graph captures
  both halves of the conversation. Skips model errors, partial streaming chunks,
  and function-call-only responses (tool-loop steps). Configurable via
  `WithAssistantMessageName` and `WithAfterLogger`.
- `NewMemoryService` — an ADK `memory.Service` backed by Zep user-graph search,
  attachable at the runner via `runner.Config.MemoryService`. Maps every
  supported search scope into results (edges → facts, nodes → entity summaries,
  episodes → message content, observations → derived memories, auto → the Context
  Block) and rejects unsupported scopes loudly. Configurable via
  `WithSearchScope`, `WithSearchLimit`, and `WithMemoryLogger`.
- `NewGraphSearchTool` — a `functiontool` (`search_memory`) the model can call to
  search the user's Zep knowledge graph on demand, with optional standalone-graph
  scoping via `WithGraphID`. Maps the same set of search scopes as the memory
  service.
- `EnsureUser` / `EnsureThread` — idempotent helpers to provision the Zep user
  and thread keyed on the ADK user ID and session ID.
- `NewClient` / `NewClientFromEnv` — Zep client constructors; `NewClientFromEnv`
  returns `nil` when `ZEP_API_KEY` is unset so the integration degrades to a
  no-op.
- `InjectSystemInstruction`, `LastUserText`, `AssistantText`,
  `IsToolLoopContinuation` — exported helpers for building custom callbacks.
- Graceful error handling throughout: a `nil` client and transient Zep errors
  never crash the host agent.
- Table-based unit tests (no network, via an internal client seam) and a runnable
  example wiring an `llmagent` and runner.

Targets `google.golang.org/adk` v1.4.0 and `github.com/getzep/zep-go/v3` v3.23.0.
