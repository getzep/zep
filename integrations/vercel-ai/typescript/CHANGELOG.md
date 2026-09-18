# Changelog

## Unreleased

### Changed

- **Breaking: the package targets the Zep v4 SDK (`@getzep/zep-cloud`
  `4.0.0-alpha.5`).** The v3 SDK is no longer supported, and the package does
  not contain a compatibility layer.
- **Breaking: the public API takes UUIDs.** Zep v4 addresses every user,
  thread, and graph by a server-generated UUID. `userId`, `threadId`, and
  `graphId` are developer-assigned names, not addresses. `ZepBinding` now has
  `graphUuid` and `threadUuid`. `createZepMiddleware`, `createZepOnFinish`,
  `createZepContextTool`, `getZepContext`, and `persistZepTurn` take
  `threadUuid`, `userUuid`, and `templateUuid`. The package does not call a
  lookup method at run time. An application resolves a name to a UUID one time
  and stores the UUID in its own database.
- **Breaking: `ensureZepUserAndThread` is replaced by
  `createZepUserAndThread`.** The new function creates the user and the thread,
  and returns `{ userUuid, graphUuid, threadUuid }` from the Zep responses, or
  `null` when Zep does not create both resources. `EnsureIdentityOptions` is
  replaced by `CreateIdentityOptions`, and the new `ZepIdentity` type is
  exported. The `onUserCreated` hook now receives the new user UUID.
- **Breaking: `toRoleType` can return `undefined`.** Zep v4 removed the
  `norole` member of `RoleType`. An unrecognized or absent role maps to
  `undefined`, and the package omits the role from the message.
- **Breaking: `resolveGraphTarget` returns `string | null`.** The function
  returns the bound graph UUID.
- **Breaking: the `searchFilters` option of `createZepSearchTool` is renamed to
  `filters`**, which matches the v4 search request body.
- Zep calls now use the v4 methods: `user.create`, `thread.create` with a
  `userUuid`, `thread.getContext`, `graph.episode.add`, and the scope-specific
  search methods `graph.searchEdges`, `graph.searchNodes`,
  `graph.searchEpisodes`, `graph.searchObservations`, and
  `graph.searchThreadSummaries`. The `auto` search scope calls
  `graph.getContext`. The search request carries `limit` at the top level and
  the query, the reranker, and the filters in a nested `body`.
- The examples, the tests, the README, and SETUP.md show the v4 API and the
  UUID identifier convention.

## 0.2.0 (2026-07-07)

### Added

- **Guaranteed persistence loop, opt-in on the middleware.** `createZepMiddleware` gains a
  `persist?: boolean | { userName?: string; assistantName?: string }` option. When set, the
  returned middleware also implements `wrapGenerate`/`wrapStream`: after the model's final
  step in a turn (`finishReason !== "tool-calls"`), it fires a fire-and-forget
  `thread.addMessages` call with the user's message (recovered from `params.prompt`) and the
  final assistant text (from `wrapGenerate`'s result content, or accumulated `text-delta`
  parts for `wrapStream`). Persistence never blocks or throws into the host call; failures
  are logged. **Use one or the other** — enabling `persist` AND wiring `createZepOnFinish` on
  the same call double-persists every turn. `createZepOnFinish` remains exported and fully
  supported for callers who prefer explicit `onFinish` wiring.
- `contextBuilder` option on `createZepMiddleware` — an optional async function
  (`ZepContextBuilder`) that replaces the default `thread.getUserContext` retrieval inside
  `transformParams`. Receives a single `ZepContextBuilderInput` (`client`, `userId`,
  `threadId`, `userMessage`, `params`); return `undefined` to inject nothing. Runs inside the
  same try/catch as the default path — a rejection is logged and degrades to "no context
  injected" for that turn. `createZepMiddleware` also gains a `userId` option, threaded to the
  builder.
- `DEFAULT_CONTEXT_TEMPLATE` — the canonical Context Block wrapper text (the explicit
  `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block), now exported and used by the default
  `formatContext`. Byte-identical to the equivalent constant in the other Zep framework
  integrations (Python, Go, TypeScript). Rendered via plain `{context}` string replacement,
  never a template literal executing user content. `formatContext` remains available as a
  full override. **This changes the default injected wording — see "Changed" below.**
- `onUserCreated` hook (`ZepUserCreatedHook`) on `EnsureIdentityOptions` — fires exactly once,
  immediately after `ensureZepUserAndThread` genuinely creates the Zep user (never on an
  already-exists/409 path). Hook errors are logged, not thrown; `ensureZepUserAndThread`'s
  `Promise<boolean>` keeps its existing "user + thread ready" meaning.
- **Pin-or-expose search tool parameters (BREAKING — see below).** `createZepSearchTool`'s
  Zod input schema now exposes `scope` (six values: `edges`, `nodes`, `episodes`,
  `observations`, `thread_summaries`, `auto`), `reranker` (five values), `limit`,
  `mmrLambda`, and `centerNodeUuid` to the model by default (previously only `query` was
  exposed; everything else was pinned at construction and hidden). New `pinnedParams` fixes a
  parameter to a constant value (hidden from the schema, always sent); new `hiddenParams`
  removes a parameter from the schema without pinning it (omitted from the SDK call so Zep's
  own default applies). `searchFilters` stays constructor-only; new `bfsOriginNodeUuids`
  constructor option joins it. The legacy `scope`/`reranker`/`limit` constructor args still
  work — they now pin (and hide) the corresponding parameter, equivalent to passing it via
  `pinnedParams`.

### Changed

- **Breaking: `createZepSearchTool`'s model-facing schema changed.** Previously the tool's
  only input was `query`; a model integrated against the old schema (e.g. a cached tool
  manifest) will see four new optional parameters. Existing callers that only relied on
  `query` and did not depend on `scope`/`reranker`/`limit` being invisible to the model are
  unaffected at the call site; callers who need the old "model only sees query" behavior
  should pass `hiddenParams: ["scope", "reranker", "limit", "mmrLambda", "centerNodeUuid"]`
  (or pin what they need via `pinnedParams`/the legacy constructor args).
- **Breaking: the default injected system-message wording changed.**
  `createZepMiddleware`'s default `formatContext` now renders the canonical
  `DEFAULT_CONTEXT_TEMPLATE` (shared verbatim across all Zep framework integrations)
  instead of the 0.1.x inline wording. The 0.1.x default was, verbatim:

  ```
  The following is relevant long-term memory about the user, retrieved from Zep. Use it to personalize and ground your response.

  <the Context Block>
  ```

  The 0.2.0 default is:

  ```
  The following context is retrieved from Zep, the agent's long-term memory. It contains relevant facts, entities, and prior knowledge about the user. Use it to inform your responses.

  <ZEP_CONTEXT>
  <the Context Block>
  </ZEP_CONTEXT>
  ```

  To restore the exact 0.1.x output, pass the old wording via `formatContext` — see
  "Migrating from 0.1.x" below.

### Migrating from 0.1.x

**Restoring the 0.1.x injected wording (default `formatContext`):**

```ts
// 0.2.0 injects the canonical <ZEP_CONTEXT> template by default. To reproduce
// the 0.1.x system-message text exactly, override formatContext:
const middleware = createZepMiddleware({
  client,
  threadId,
  formatContext: (context) =>
    "The following is relevant long-term memory about the user, retrieved from " +
    "Zep. Use it to personalize and ground your response.\n\n" +
    context,
});
```

**Restoring the old "model only sees query" search tool schema:**

```ts
// Before (0.1.x) — scope/limit/reranker were always pinned and hidden
const tool = createZepSearchTool({ client, binding, scope: "edges", limit: 10 });

// After (0.2.0) — equivalent: pin (and hide) every other parameter explicitly
const tool = createZepSearchTool({
  client,
  binding,
  pinnedParams: { scope: "edges", limit: 10 },
  hiddenParams: ["reranker", "mmrLambda", "centerNodeUuid"],
});
```

**Opting into guaranteed persistence (optional):**

```ts
// Before (0.1.x) — inject via middleware, persist via onFinish
const model = wrapLanguageModel({ model: openai("gpt-4o-mini"), middleware: createZepMiddleware({ client, threadId }) });
const { text } = await generateText({ model, prompt, onFinish: createZepOnFinish({ client, threadId, user: prompt }) });

// After (0.2.0) — persist: true folds persistence into the middleware; drop onFinish
const model = wrapLanguageModel({ model: openai("gpt-4o-mini"), middleware: createZepMiddleware({ client, threadId, persist: true }) });
const { text } = await generateText({ model, prompt });
```

## 0.1.0 (2026-06-17)

### Added

- Initial release of `@getzep/zep-vercel-ai` — Zep long-term memory for the
  Vercel AI SDK (v6).
- `createZepMiddleware` — a context-injection `LanguageModelMiddleware`
  (`specificationVersion: "v3"`) for `wrapLanguageModel`. `transformParams`
  injects the user's Context Block (`thread.getUserContext`) as a system message
  on each genuine new user turn (detected by the last prompt message being a
  `user` message), on both `generate`/`stream` calls — so the block is fetched
  at most once per turn, not once per tool-loop step. Injection only;
  persistence lives in `createZepOnFinish`. Customizable via `formatContext`,
  `templateId`, and `logger`.
- `createZepOnFinish` — builds an AI SDK `onFinish` callback that persists the
  whole turn (user input + final assistant text) exactly once per turn, for both
  `generateText` and `streamText`.
- `getZepContext` and `persistZepTurn` — plain async helpers for the `system:` +
  `onFinish` pattern.
- `createZepTools` — builds `{ zepSearch, zepRemember, zepContext }` model-
  callable tools (AI SDK `tool()` + Zod `inputSchema`) bound to one client and
  binding, plus the standalone factories `createZepSearchTool`,
  `createZepRememberTool`, `createZepContextTool`.
- `ensureZepUserAndThread` — idempotent helper to provision the Zep user and
  thread before the first turn.
- `toRoleType`, `resolveGraphTarget`, `truncateForZep`, `MESSAGE_MAX_CHARS`, and
  `GRAPH_MAX_CHARS` utilities; `ZepBinding`, `ZepLogger`, `ZepTurn`, and
  `RoleType` types.
- User-graph (`userId`) and standalone-graph (`graphId`) bindings.
- Truncation with lengths-only warnings (no content/PII in logs), never silent
  drops: 4,096-char limit on the conversational `thread.addMessages` path and
  10,000-char limit on the `graph.add` path.
- Graceful error handling across every layer — a Zep failure is logged and never
  crashes the host call.
- Mock-based test suite plus a live test gated on `ZEP_API_KEY`.
- Runnable `generateText` and `streamText` examples, README, and SETUP guide.

### Compatibility

- Targets the Vercel AI SDK **v6** (`ai` >= 6, the `v3` middleware/provider
  interfaces) — **not** compatible with AI SDK v5.
- `zod` >= 3.25 (peer); Zep V3 (`@getzep/zep-cloud` >= 3.23.0).
