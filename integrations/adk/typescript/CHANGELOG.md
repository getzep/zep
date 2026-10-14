# Changelog

## 1.0.0

### Breaking

Follow the [V4 migration guide](https://help.getzep.com/migrating-from-v3) for the general migration procedure: the UUID columns, the one-time backfill, and the order of steps.

These API changes are specific to this package:

- The package requires `@getzep/zep-cloud@^4.0.0` (Zep V4, below V5).
- `ensureUser(zep, { userId, ... })` is replaced by `createUser(zep, { firstName, lastName, email, onCreated })`. It has no `userId` option, and it returns `{ userUuid, graphUuid }` instead of a value that tells whether the user was new. The `onCreated` hook receives the UUID of the new user.
- `ensureThread(zep, { threadId, userId })` is replaced by `createThread(zep, { userUuid })`. It has no `threadId` option, and it returns `{ threadUuid, graphUuid }`.
- The `userId` and `threadId` options are replaced by `userUuid` and `threadUuid` on `createZepCallbacks`, `createZepBeforeModelCallback`, `createZepAfterModelCallback`, `ZepContextTool`, `ZepGraphSearchTool`, and `resolveIdentity`. Give the stored user UUID and thread UUID to these options.
- The session-state keys `zep_user_id` and `zep_thread_id` are replaced by `zep_user_uuid` and `zep_thread_uuid`. To use one agent for many users, put the stored UUIDs in these keys. If you omit them, the ADK session `userId` and `sessionId` must be the Zep user UUID and the Zep thread UUID.
- The `userId` and `threadId` fields of `ResolvedIdentity` and `ContextBuilderInput` are replaced by `userUuid` and `threadUuid`.
- The `graphId` option of `ZepGraphSearchTool` is replaced by `graphUuid`. `ZepMemoryService` has a new `graphUuid` option. Give the stored graph UUID to these options. If you omit `graphUuid`, the package reads the graph of the user UUID with `zep.user.get`.
- `searchFilters` on `ZepGraphSearchTool` takes the camelCase V4 `Zep.SearchFilters` shape (for example `dateFilters` and `metadataFilters`). Each search scope accepts only the filter keys that apply to it, and Zep returns HTTP 400 `unsupported_filter` for another key.

For an existing V3 user, thread, or graph, do not call `createUser`, `createThread`, or `graph.create`. Get its UUID one time with the backfill in the migration guide, and store it.

### Changed

- `ZepGraphSearchTool` and `ZepMemoryService` read one page of results from the v4 search methods, which replaces the v3 page number and cursor behavior.
- `ZepGraphSearchTool` checks each scope and reranker pair before the request. The model sees only the rerankers that the tool can use, and the tool returns an error message that names the valid values for a pair that Zep rejects. The constructor throws an error for pinned options that cannot work together. The tool sends `mmrLambda: 0.5` when the reranker is `"mmr"` and no value is set.
- `ZepGraphSearchTool` no longer ignores a reranker with the `auto` scope. The `auto` scope takes no reranker. When the model sends a reranker with `auto`, the tool does not call Zep, and it returns an error message that tells the model to call the tool again with no reranker or with a scope that accepts the reranker. The constructor throws an error when `scope` is pinned to `"auto"` and a reranker is pinned. When a reranker is pinned, the tool does not show the `auto` scope.
- The `reranker` parameter of `ZepGraphSearchTool` has no default value. When the model sends no reranker, the tool sends no reranker, and Zep uses `"rrf"`. When the model sends a value that is not a reranker, the tool does not call Zep, and it returns an error message that lists the valid rerankers.

### Added

- `graphUuid` option on `ZepMemoryService`, which sets a fixed graph for every `searchMemory` call.
- `lint` script (`eslint .`) and an ESLint 9 configuration.

## 0.2.0 (2026-07-06)

### Added

- `ZepMemoryService` — an ADK-native `BaseMemoryService` implementation for wiring Zep into `Runner`'s `memoryService` option, so the model can search Zep on demand via ADK's built-in `loadMemory`/`preloadMemory` tools. Supports all six graph search scopes (`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto`) with a configurable `scope`/`limit`. `addSessionToMemory` is an intentional no-op, since Zep already ingests conversation turns live via `ZepContextTool`/`createZepBeforeModelCallback`+`createZepAfterModelCallback` — see the README's "ADK-native memory service" section.
- `ensureUser` / `ensureThread` — idempotent, out-of-band helpers to provision the Zep user and thread before the first turn, replacing lazy in-band creation (see Changed). Both resolve to whether the resource was newly created, and `ensureUser` accepts an optional `onCreated` hook that fires exactly once, only for genuinely new users.
- `contextTemplate` / `DEFAULT_CONTEXT_TEMPLATE` — configurable template wrapping the injected Zep context block, canonical across Python, Go, and TypeScript. `formatContextInstruction` gained a second, optional `template` parameter.
- `searchFilters` and `bfsOriginNodeUuids` — new constructor-only options on `ZepGraphSearchTool` for scoped/BFS-seeded search; additive, default to unset.

### Changed

- **Breaking: `ZepGraphSearchTool` is now pin-or-expose instead of always-pinned.** Any of `scope`, `reranker`, `limit`, `mmrLambda`, `centerNodeUuid` you *omit* is now exposed to the model with the same default, so the model can choose it per call. Previously these were always pinned at construction and the model's tool schema only ever contained `query`. See the README's "Migrating from 0.1.x" section for the recipe to restore the old fully-pinned behavior.
- **Breaking: lazy creation removed.** `createZepBeforeModelCallback`, `createZepAfterModelCallback`, and `ZepContextTool` no longer call `zep.user.add` / `zep.thread.create`. Provision the user and thread explicitly with `ensureUser` / `ensureThread` before the first turn.
- **Breaking: `ZepResourceManager` removed**, replaced by the `ensureUser` / `ensureThread` functions (provisioning) and an internal dedup guard. Callers sharing a `resources: ZepResourceManager` instance between callbacks should pass `dedup` instead, or use `createZepCallbacks`, which wires it automatically.
- **Breaking: `email` removed from the identity surface** (`ZepIdentityOptions`, `ResolvedIdentity`, and the `zep_email` session-state key). With lazy creation gone, the turn path never touched the Zep user profile, so these were silently inert. Pass `email` to `ensureUser` during provisioning instead.
- **Breaking: the default injected context wording changed.** The hardcoded 0.1.x header ("...Use it to inform your response.", singular) is replaced by `DEFAULT_CONTEXT_TEMPLATE` ("...Use it to inform your responses.", plural), now overridable via `contextTemplate`. The `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper is unchanged.
- `@google/adk` peer dependency loosened from an exact `1.2.0` pin to `^1.2.0` — the `1.x` line is verified stable across all surfaces this package uses.

## 0.1.0 (2026-06-16)

### Added

- `createZepBeforeModelCallback` — primary `beforeModelCallback` factory that persists the latest user message to Zep and injects the retrieved Context Block into `LlmRequest.config.systemInstruction`.
- `createZepAfterModelCallback` — `afterModelCallback` factory that persists assistant responses to the Zep thread, skipping tool-call turns and partial streaming chunks.
- `ZepContextTool` — a `BaseTool` subclass offering the same persist-and-inject behaviour via `processLlmRequest`, as a tool-centric alternative to the callback.
- `ZepGraphSearchTool` — a model-callable `BaseTool` for on-demand knowledge-graph search, with support for user graphs and standalone graphs.
- Identity resolution from explicit options, ADK session state (`zep_user_id`, `zep_thread_id`, `zep_first_name`, `zep_last_name`, `zep_email`), and ADK session metadata.
- Lazy Zep user and thread creation with an "already exists" guard and a per-process cache.
- Graceful error handling: every Zep call is wrapped so a Zep failure is logged but never crashes the host agent.
- Mock-based test suite (Vitest) and two runnable examples.
- Built against `@google/adk@1.2.0` and `@getzep/zep-cloud@^3.23.0` (Zep V3), ESM, NodeNext + strict.
