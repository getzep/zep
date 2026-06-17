# Changelog

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
