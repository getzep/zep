# Changelog

## 0.1.0 (2026-06-17)

### Added

- Initial release of `@getzep/zep-vercel-ai` — Zep long-term memory for the
  Vercel AI SDK (v6).
- `createZepMiddleware` — a `LanguageModelMiddleware` (`specificationVersion:
  "v3"`) for `wrapLanguageModel`. `transformParams` injects the user's Context
  Block (`thread.getUserContext`) as a system message on every `generate`/
  `stream` call; with `persist: true`, `wrapGenerate` records the user+assistant
  turn via `thread.addMessages` for non-streaming `generateText`. Customizable
  via `formatContext`, `templateId`, `userName`/`assistantName`, and `logger`.
- `getZepContext` and `persistZepTurn` — plain async helpers for the `system:` +
  `onFinish` pattern, which is the required persistence path for `streamText`.
- `createZepTools` — builds `{ zepSearch, zepRemember, zepContext }` model-
  callable tools (AI SDK `tool()` + Zod `inputSchema`) bound to one client and
  binding, plus the standalone factories `createZepSearchTool`,
  `createZepRememberTool`, `createZepContextTool`.
- `ensureZepUserAndThread` — idempotent helper to provision the Zep user and
  thread before the first turn.
- `toRoleType`, `resolveGraphTarget`, `truncateForZep`, and `MESSAGE_MAX_CHARS`
  utilities; `ZepBinding`, `ZepLogger`, `ZepTurn`, and `RoleType` types.
- User-graph (`userId`) and standalone-graph (`graphId`) bindings.
- 4,096-char message truncation with lengths-only warnings (no content/PII in
  logs) in both the middleware and the helpers/tools.
- Graceful error handling across every layer — a Zep failure is logged and never
  crashes the host call.
- Mock-based test suite plus a live test gated on `ZEP_API_KEY`.
- Runnable `generateText` and `streamText` examples, README, and SETUP guide.

### Compatibility

- Targets the Vercel AI SDK **v6** (`ai` >= 6, the `v3` middleware/provider
  interfaces) — **not** compatible with AI SDK v5.
- `zod` >= 3.25 (peer); Zep V3 (`@getzep/zep-cloud` >= 3.23.0).
