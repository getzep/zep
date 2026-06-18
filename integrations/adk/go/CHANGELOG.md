# Changelog

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
