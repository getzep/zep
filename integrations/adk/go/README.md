# Zep Google ADK (Go) Integration

`zepadk` gives [Google Agent Development Kit (ADK) for Go](https://github.com/google/adk-go)
agents persistent, cross-session memory backed by [Zep](https://www.getzep.com),
Zep's temporal Context Graph platform for agent memory.

It plugs into ADK's native extension points — it does **not** wrap or replace the
ADK runtime:

- **Context injection** via an `llmagent.BeforeModelCallback` that persists each
  user turn to Zep and injects the user's Context Block into the model request.
- **Memory service** — an ADK `memory.Service` backed by Zep user-graph search,
  attached at the runner and reached by tools through `ToolContext.SearchMemory`.
- **On-demand recall** — a `functiontool` the model can call to search the user's
  Zep knowledge graph.

## Installation

```bash
go get github.com/getzep/zep/integrations/adk/go@latest
```

```go
import zepadk "github.com/getzep/zep/integrations/adk/go"
```

Requirements: Go 1.23+, `google.golang.org/adk` v1.4.0, `github.com/getzep/zep-go/v3` v3.23.0.

## Quick start

```go
zep := zepadk.NewClientFromEnv() // nil when ZEP_API_KEY is unset -> no-op

agent, _ := llmagent.New(llmagent.Config{
    Name:                 "assistant",
    Model:                llm, // a model.LLM, e.g. gemini.NewModel(...)
    BeforeModelCallbacks: []llmagent.BeforeModelCallback{zepadk.NewBeforeModelCallback(zep)},
    Tools:                []tool.Tool{searchTool}, // from zepadk.NewGraphSearchTool(zep)
})

run, _ := runner.New(runner.Config{
    AppName:        "my_app",
    Agent:          agent,
    SessionService: sessions,
    MemoryService:  zepadk.NewMemoryService(zep),
})
```

See [`examples/main.go`](examples/main.go) for a complete, runnable wiring of the
agent, runner, session service, and Zep user/thread provisioning.

## How it works

The integration contract maps ADK identifiers to Zep identifiers:

| ADK | Zep |
|-----|-----|
| session ID | thread ID |
| user ID | user ID (user graph) |

Provision the Zep user and thread out of band before the first turn with
`EnsureUser` and `EnsureThread` (both idempotent). Then, on every model turn, the
callback returned by `NewBeforeModelCallback`:

1. Reads the user's latest message from the ADK callback context.
2. Persists it to the user's Zep thread, requesting the Context Block in the same
   round-trip (`Thread.AddMessages` with `ReturnContext=true`).
3. Injects the returned Context Block into `req.Config.SystemInstruction`.

The public surface lives in:

- [`zepadk.go`](zepadk.go) — `NewBeforeModelCallback`, `EnsureUser`, `EnsureThread`,
  and the injection helpers `InjectSystemInstruction` / `LastUserText`.
- [`memory.go`](memory.go) — `NewMemoryService` (the ADK `memory.Service` over
  Zep `Graph.Search`).
- [`tool.go`](tool.go) — `NewGraphSearchTool` (the on-demand `search_memory` tool).
- [`client.go`](client.go) — `NewClient` / `NewClientFromEnv`.

## Configuration

Each constructor accepts functional options:

| Constructor | Options |
|-------------|---------|
| `NewBeforeModelCallback` | `WithContextPrefix`, `WithUserMessageName`, `WithLogger` |
| `NewMemoryService` | `WithSearchScope`, `WithSearchLimit`, `WithMemoryLogger` |
| `NewGraphSearchTool` | `WithToolName`, `WithToolDescription`, `WithGraphID`, `WithToolSearchScope`, `WithToolSearchLimit`, `WithToolLogger` |

`WithGraphID` scopes the search tool to a standalone graph instead of the calling
user's graph (`UserID` and `GraphID` are mutually exclusive in Zep).

## Error handling

A Zep failure never crashes the host agent:

- A `nil` client (for example when `ZEP_API_KEY` is unset) makes the callback,
  memory service, and tool safe no-ops.
- Transient Zep errors are logged via the configured `slog.Logger` and swallowed;
  the callback proceeds to the model without injected memory, and the memory
  service and tool return empty results.

## Notes

- **Ingestion is asynchronous.** A message added during a turn is not guaranteed
  to be retrievable within that same turn; the returned Context Block reflects
  prior turns. Design for eventual availability.
- **Reuse one client** across the lifetime of the process.
- Pass real user names (and ideally last name + email) to `EnsureUser` so Zep
  resolves the user's identity in the graph.

## Development

```bash
make all     # tidy + fmt-check + vet + lint + test
make test    # go test ./...
```

`make lint` runs `golangci-lint` if it is installed.

## Support

- [Zep documentation](https://help.getzep.com)
- [Google ADK for Go](https://github.com/google/adk-go)
- [GitHub issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see the repository [LICENSE](../../../LICENSE).
