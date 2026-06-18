# Changelog

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
