# Changelog

## 1.0.0

### Breaking

To upgrade from Zep v3, follow the [V4 migration guide](https://help.getzep.com/migrating-from-v3) for the general procedure. This entry gives only the changes that are specific to this package.

- **The package requires the Zep v4 SDK (`@getzep/zep-cloud` `^4.0.0`, which is 4.0.0 or later and below 5).** The public API of the package takes UUIDs, and the package never calls `lookup` at run time.
- **The bindings take UUIDs.** `ZepBinding` takes `graphUuid` in place of `userId`/`graphId`, and `ZepThreadBinding` takes `threadUuid` in place of `threadId`. Give the stored UUIDs in the `binding` option of `createZepToolset`, `createZepRememberTool`, `createZepSearchTool`, and `createZepContextTool`. For a user graph, give the `graphUuid` of the user. For a standalone graph, give the `uuid` of the graph.
- **`createZepProcessors`, `ZepInputProcessor`, and `ZepOutputProcessor` take `graphUuid` and `threadUuid`** in place of `userId` and `threadId`. `ZepContextBuilderInput` gives `graphUuid` and `threadUuid` to a custom context builder.
- **`ResolvedZepIdentity` returns `graphUuid` and `threadUuid`** in place of `userId` and `threadId`. A `resolveIdentity` function must return the stored UUIDs.
- **`ensureZepUserAndThread` is replaced by `createZepUserAndThread`, and `EnsureIdentityOptions` is replaced by `CreateIdentityOptions`.** The new function accepts no `userId` and no `threadId`. It creates a new user and a new thread, and returns `ZepIdentity` (`{ userUuid, graphUuid, threadUuid }`), or `null` after a failure. It is not idempotent: a second call creates a second user. The `onUserCreated` hook receives the `userUuid`.
- **`createZepContextTool` and the processors take `templateUuid`** in place of `templateId`.
- **`createZepSearchTool` takes `filters`** in place of `searchFilters`. Zep rejects a filter key that the selected scope cannot apply with HTTP 400 `unsupported_filter`.
- `resolveGraphTarget` is renamed to `resolveGraphUuid` and returns the bound `graphUuid`.
- `toRoleType` no longer maps an unknown role to `norole`, because Zep v4 removed that role. An unknown role is omitted, and the tool records the message without a role name.

For an existing v3 user, thread, or graph, do not call `createZepUserAndThread` or `graph.create`. Get its UUID one time with the backfill in the migration guide, and store it.

### Changed

- The Zep calls are migrated to their v4 replacements: `thread.getUserContext` to `thread.getContext`, `graph.add` to `graph.episode.add`, `user.add` to `user.create`, and `graph.search` to the scope methods `graph.searchEdges`, `graph.searchNodes`, `graph.searchEpisodes`, `graph.searchObservations`, `graph.searchThreadSummaries`, and `graph.getContext` for the `auto` scope. The `zepSearch` tool keeps the model-visible `scope` parameter and maps each paginated result to its facts.
- `zepSearch` validates the scope and reranker pair before the request, because each Zep v4 search method accepts only some rerankers. The schema shows only the valid choices, an invalid model choice returns an `error` that names the valid choices, an invalid pinned pair throws at construction, and `mmr` sends a default `mmrLambda` of 0.5.
- `zepSearch` does not ignore a reranker with the `auto` scope, because `graph.getContext` takes no reranker. A model-selected reranker with `auto` returns an `error` that tells the model to search again with no reranker or with a scope that accepts the reranker, and the tool does not call Zep. A pinned `scope: "auto"` with a pinned reranker throws at construction. The tool sends the default `rrf` reranker only for the other scopes.

## 0.2.0 (2026-07-07)

### Added

- **`ZepInputProcessor` / `ZepOutputProcessor` / `createZepProcessors`** — an automatic memory loop wired directly into Mastra's native `inputProcessors`/`outputProcessors` pipeline, closing out the spec of ZEPAI-3098. `ZepInputProcessor` retrieves a Zep Context Block for the latest user message and injects it as a system message before the model call; `ZepOutputProcessor` persists the completed user/assistant turn to the bound Zep thread afterward, skipping `finishReason === "tool-calls"` turns. Both degrade gracefully on any Zep failure (log + pass through/skip) and never call `abort()` or throw into the agent loop. Because input and output processors sit on opposite sides of the model call, running both together gets the same persist/inject concurrency ADK's `beforeModelCallback`/`afterModelCallback` pair provides — for free. This is now the recommended entry point; see the README's "Automatic memory (processors)" section.
- `contextBuilder` / `ZepContextBuilder` / `ZepContextBuilderInput` — replace `thread.getUserContext` on `ZepInputProcessor` with a custom async builder for the injected Context Block.
- `contextTemplate` / `DEFAULT_CONTEXT_TEMPLATE` / `formatContext` — configurable rendering of the injected context. `DEFAULT_CONTEXT_TEMPLATE` is canonical across Zep's Python, Go, and TypeScript framework integrations. `{context}` is replaced via plain string splitting (never a regex), so context text containing `{`, `}`, `%`, or `$` is always safe to inject.
- `resolveIdentity` / `ZepIdentityResolver` — per-call identity resolution from Mastra's `requestContext`, accepted by both processors and every tool (`createZepSearchTool`, `createZepRememberTool`, `createZepContextTool`), and forwarded to all three tools by `createZepToolset`. Falls back to the constructor-bound `userId`/`threadId` when unset or when the resolver omits a field. Lets a single shared processor/tool instance serve many end users.
- `onUserCreated` / `ZepUserCreatedHook` on `ensureZepUserAndThread` — fires exactly once, only when the Zep user was genuinely newly created (never on an already-exists conflict). Hook errors are logged and swallowed; they never flip a successful user creation to a reported failure.
- `pinnedParams` / `hiddenParams` on `createZepSearchTool` — the new tri-state pin-or-expose parameter model (see Changed) alongside `bfsOriginNodeUuids`, a new constructor-only option for BFS-seeded search.

### Changed

- **Breaking: `createZepSearchTool` is now pin-or-expose instead of always-pinned.** Previously the model's tool schema only ever contained `query`, with `scope`/`limit`/`reranker`/`searchFilters` fixed at construction. Now `scope`, `reranker`, `limit`, `mmrLambda`, and `centerNodeUuid` are each exposed to the model by default (with Zep's documented defaults: `scope: "edges"`, `reranker: "rrf"`, `limit: 10`), so the model can choose them per call. The old constructor args `scope`/`reranker`/`limit` still work and now pin (and hide) their parameter exactly as before — see the README's "Migrating to 0.2.0" section to restore the fully-pinned behavior explicitly via `pinnedParams`. `searchFilters` remains constructor-only.
- **Bug fix: `ensureZepUserAndThread` no longer swallows genuine Zep failures.** The previous implementation matched a message regex (`/exist|conflict|409|duplicate/i`) inside a blanket catch, which mis-treated some genuine failures (e.g. a 500 whose message happened to mention "conflict") as success, and logged real errors (401s, 5xxs) at `debug` instead of `warn`. It now uses typed already-exists detection (a `statusCode === 409` conflict, or a `400` whose message says "already exists") with two independent create-then-catch-conflict steps for user and thread; any other status code is a genuine failure, logged at `warn`, and reported via `false` (the `Promise<boolean>` return shape is unchanged — this is a behavior fix, not a signature change).
- README rewritten to lead with the processors (automatic memory) and cover the tool-only surface second, matching the ADK integration's structure.

## 0.1.0 (2026-06-16)

### Added

- Initial release of `@getzep/zep-mastra`.
- `createZepRememberTool` — Mastra tool that persists messages/facts to Zep via
  `thread.addMessages` (conversational) or `graph.add` (general data).
- `createZepSearchTool` — model-callable Mastra tool that searches the bound Zep
  graph via `graph.search` and returns relevant facts; scope, limit, reranker,
  and filters are pinned at construction.
- `createZepContextTool` — Mastra tool that returns the whole-user-graph Context
  Block via `thread.getUserContext`.
- `createZepToolset` — builds all three tools bound to a single client and
  binding, keyed for direct use as an Agent `tools` record.
- `ensureZepUserAndThread` — idempotent helper to provision the Zep user and
  thread before the first turn.
- `toRoleType` / `resolveGraphTarget` utilities; `ZepBinding`,
  `ZepThreadBinding`, and `ZepLogger` types.
- User-graph (`userId`) and standalone-graph (`graphId`) bindings.
- Graceful error handling across all tools — a Zep failure is logged and never
  crashes the host agent.
- Mock-based test suite plus a live test gated on `ZEP_API_KEY`.
- Runnable example (`examples/basic-agent.ts`), README, and SETUP guide.

### Compatibility

- Targets Zep V3 (`@getzep/zep-cloud` >= 3.23.0) and `@mastra/core` >= 1.42.0.
