# Changelog

## v1.0.0

### Breaking

To move an application from Zep V3 to V4, follow the
[Zep V4 migration guide](https://help.getzep.com/migrating-from-v3) for the
general procedure: the nullable UUID columns, the one-time `lookup` backfill,
and the order of steps. This section lists only the changes that are specific
to this package.

- The package requires the Zep V4 SDK, `github.com/getzep/zep-go/v4` v4.0.0
  or later and below v5, in place of `github.com/getzep/zep-go/v3`. The
  module path of this integration does not change.
- `EnsureUser(ctx, client, userID, firstName, lastName, email)` is removed.
  Use `CreateUser(ctx, client, firstName, lastName, email)` for a new user
  only. It returns the user UUID and the graph UUID of the user.
- `EnsureThread(ctx, client, threadID, userID)` is removed. Use
  `CreateThread(ctx, client, userUUID)` for a new thread only. It returns the
  thread UUID.
- Give the stored thread UUID to `NewBeforeModelCallback` with
  `WithThreadUUID` or `WithThreadUUIDResolver`, and to
  `NewAfterModelCallback` with `WithAfterThreadUUID` or
  `WithAfterThreadUUIDResolver`. The callbacks no longer use the ADK session
  ID as the Zep thread ID.
- Give the stored user UUID to `NewBeforeModelCallback` with `WithUserUUID`
  or `WithUserUUIDResolver`. The callback gives it to a custom
  `ContextBuilder` in `ContextInput.UserUUID`.
- Give the stored graph UUID to `NewGraphSearchTool` with `WithGraphUUID` or
  `WithGraphUUIDResolver`, and to `NewMemoryService` with
  `WithMemoryGraphUUID` or `WithMemoryGraphUUIDResolver`. `WithGraphID` is
  removed. The tool and the memory service no longer use the ADK user ID as
  the Zep user ID.
- The `UserID` and `ThreadID` fields of `ContextInput` are replaced by
  `UserUUID` and `ThreadUUID`.
- A callback, the tool, or the memory service that has no UUID logs an error
  and returns no result.
- The search scope is now the package type `zepadk.SearchScope`
  (`SearchScopeEdges`, `SearchScopeNodes`, `SearchScopeEpisodes`,
  `SearchScopeObservations`, `SearchScopeThreadSummaries`, and
  `SearchScopeAuto`), because Zep v4 has one search method for each scope and
  exports no scope enum. `WithSearchScope` and `WithToolSearchScope` take
  this type instead of `zep.GraphSearchScope`.
- The reranker is now the package type `zepadk.SearchReranker`
  (`SearchRerankerRRF`, `SearchRerankerMMR`, `SearchRerankerNodeDistance`,
  `SearchRerankerEpisodeMentions`, and `SearchRerankerCrossEncoder`), because
  Zep v4 has a different reranker type for each scope. `WithToolReranker`
  takes this type instead of `zep.Reranker`.
- `WithToolSearchScope` and `WithToolSearchLimit` now pin and hide the scope
  or limit parameter (a fixed value that is removed from the tool schema of
  the model). When the option is absent, the model can set the parameter,
  with the default `scope="edges"` and `limit=10`. To keep the parameters
  hidden, pass `WithToolSearchScope` and `WithToolSearchLimit`, and add
  `WithHiddenParams` for `reranker`, `mmr_lambda`, and `center_node_uuid`.
  The README section "Graph search tool: pin-or-expose parameters" shows the
  procedure.
- The default context block of `NewBeforeModelCallback` is now wrapped in
  `DefaultContextTemplate` (a `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block with
  framing text), not in the bare `DefaultContextPrefix` string. To keep the
  old output, use `WithContextTemplate` or the deprecated
  `WithContextPrefix`.

For an existing V3 user, thread, or graph, do not call `CreateUser`,
`CreateThread`, or `Graph.Create`. Get its UUID one time with the backfill in
the migration guide, and store it.

### Added

- `NewGraphSearchTool` builds an explicit JSON schema (with
  `github.com/google/jsonschema-go`). Each search parameter that the model can
  set (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) can be
  exposed (the default), pinned, or hidden. New options: `WithToolReranker`,
  `WithToolMMRLambda`, `WithToolCenterNodeUUID`, `WithToolSearchFilters`,
  `WithToolBFSOriginNodeUUIDs`, and `WithHiddenParams`.
- The memory service and the search tool support the `thread_summaries`
  scope. The `scope` enum of the model has all six scopes: `edges`, `nodes`,
  `episodes`, `observations`, `thread_summaries`, and `auto`.
- `WithContextBuilder` sets a custom `ContextBuilder` function that makes the
  context block of `NewBeforeModelCallback`. Message persistence and the
  builder run at the same time. A builder error skips the injection for that
  turn only. A persistence error does not stop the injection of a builder
  result.
- `WithContextTemplate` and `DefaultContextTemplate` set the template that
  wraps the injected context block. The package renders the template with
  `strings.ReplaceAll`, so context that contains `%`, `{`, or `}` is safe.
- `ContextInput` is the input of a custom `ContextBuilder`. It has the Zep
  client, the user UUID, the thread UUID, the text of the user message, the
  ADK callback context, and the outgoing `*model.LLMRequest`.
- `NewGraphSearchTool` validates the scope and reranker pair against the
  pairs that Zep v4 accepts. The schema shows only the rerankers that a pinned
  or hidden scope accepts, and only the scopes that a pinned reranker accepts.
  A pair from the model that Zep rejects returns an error to the model that
  names the setting and the values to use, and the tool makes no Zep call.
  A pinned pair that Zep rejects makes `NewGraphSearchTool` return an error.
  The `mmr` reranker uses `mmr_lambda=0.5` when no value is set.
- With a non-nil client and no graph UUID, `NewGraphSearchTool` returns an
  error. `NewBeforeModelCallback`, `NewAfterModelCallback`, and
  `NewMemoryService` log an error when they are constructed with no thread
  UUID or graph UUID. An empty UUID string counts as no UUID.

### Changed

- `WithContextPrefix` is deprecated. Use `WithContextTemplate`.
  `WithContextPrefix` sets the template to `prefix + "{context}"`, so it
  gives the old prefix-only output. `DefaultContextPrefix` stays exported.
- The graph search tool does not offer the `episode_mentions` reranker to the
  model, because Zep requires the `EpisodeUUIDs` search filter for it. An
  `episode_mentions` value from the model falls back to the default reranker
  and is not sent to Zep. To use `episode_mentions`, set `WithToolReranker`
  and set `EpisodeUUIDs` in `WithToolSearchFilters`.
- Each Zep v4 search scope accepts only some filter keys. When the filters of
  `WithToolSearchFilters` have a key that the selected scope cannot apply, the
  search tool logs an error, makes no Zep call, and returns no facts.
- A search sends the configured limit as the Zep v4 `page_size`, up to the
  Zep maximum of 50.
- The memory service and the search tool read every page of a Zep v4 search
  result, up to the configured limit.

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
