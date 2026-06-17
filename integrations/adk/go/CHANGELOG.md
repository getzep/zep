# Changelog

## 0.1.0 (2026-06-16)

### Added

- `NewBeforeModelCallback` — an ADK `llmagent.BeforeModelCallback` that persists
  each user turn to a Zep thread and injects the returned Context Block into the
  model request's system instruction (`Thread.AddMessages` with
  `ReturnContext=true`). Configurable via `WithContextPrefix`,
  `WithUserMessageName`, and `WithLogger`.
- `NewMemoryService` — an ADK `memory.Service` backed by Zep user-graph search,
  attachable at the runner via `runner.Config.MemoryService`. Configurable via
  `WithSearchScope`, `WithSearchLimit`, and `WithMemoryLogger`.
- `NewGraphSearchTool` — a `functiontool` (`search_memory`) the model can call to
  search the user's Zep knowledge graph on demand, with optional standalone-graph
  scoping via `WithGraphID`.
- `EnsureUser` / `EnsureThread` — idempotent helpers to provision the Zep user
  and thread keyed on the ADK user ID and session ID.
- `NewClient` / `NewClientFromEnv` — Zep client constructors; `NewClientFromEnv`
  returns `nil` when `ZEP_API_KEY` is unset so the integration degrades to a
  no-op.
- `InjectSystemInstruction` / `LastUserText` — exported helpers for building
  custom callbacks.
- Graceful error handling throughout: a `nil` client and transient Zep errors
  never crash the host agent.
- Table-based unit tests (no network) and a runnable example wiring an
  `llmagent` and runner.

Targets `google.golang.org/adk` v1.4.0 and `github.com/getzep/zep-go/v3` v3.23.0.
