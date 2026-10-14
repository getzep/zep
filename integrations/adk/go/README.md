# Zep Google ADK Integration for Go

`zepadk` connects Google ADK agents to Zep. It persists user and assistant
messages, adds thread context to model requests, and supports graph search.

The integration uses the Zep V4 Go SDK. Zep creates user and thread UUIDs.
Store those UUIDs with the ADK user and session identifiers in your database.

## Requirements

- Go 1.25 or later.
- `google.golang.org/adk` v1.4.0.
- `github.com/getzep/zep-go/v4` v4.0.0.

Install the integration:

```bash
go get github.com/getzep/zep/integrations/adk/go@latest
```

## Quick start

```go
client := zepadk.NewClientFromEnv()

userUUID, graphUUID, err := zepadk.CreateUser(
    ctx, client, "Jane", "Smith", "jane@example.com",
)
if err != nil {
    return err
}
threadUUID, err := zepadk.CreateThread(ctx, client, userUUID)
if err != nil {
    return err
}

before := zepadk.NewBeforeModelCallback(client,
    zepadk.WithUserUUID(userUUID),
    zepadk.WithThreadUUID(threadUUID),
)
after := zepadk.NewAfterModelCallback(client,
    zepadk.WithAfterThreadUUID(threadUUID),
)
search, err := zepadk.NewGraphSearchTool(client,
    zepadk.WithGraphUUID(graphUUID),
)
if err != nil {
    return err
}

run, err := runner.New(runner.Config{
    AppName:        "my_app",
    Agent:          agent,
    SessionService: sessions,
    MemoryService: zepadk.NewMemoryService(
        client, zepadk.WithMemoryGraphUUID(graphUUID),
    ),
})
```

See [`examples/main.go`](examples/main.go) for a runnable agent.

## UUIDs and callbacks

Call `CreateUser` and `CreateThread` before the first agent turn. `CreateUser`
returns the user UUID and graph UUID. `CreateThread` returns the thread UUID.
Both helpers use server-generated UUIDs. They do not accept client-chosen IDs.

Pass UUIDs with `WithUserUUID`, `WithThreadUUID`, and `WithAfterThreadUUID`.
Each option has a matching resolver option. The before-model callback also
reads `zep_user_uuid` and `zep_thread_uuid` from ADK session state when an
option does not provide a UUID.

The callbacks persist user and assistant messages. The before-model callback
also returns the thread Context Block to the model request. Tool-loop
continuations do not persist the user message again. Zep errors are logged and
do not stop the agent turn.

Use `WithContextBuilder` to supply a custom context builder. The callback runs
message persistence and the builder independently. A builder failure skips
context injection. A persistence failure does not prevent a successful builder
result from being injected.

Use `WithContextTemplate` to format the context block. The template must
contain `{context}`. The deprecated `WithContextPrefix` option remains
available.

`WithIgnoreRoles` and `WithAfterIgnoreRoles` configure roles that Zep will
exclude from graph extraction. Zep still stores those messages.

## Graph search

`NewGraphSearchTool` creates a model-callable graph search tool. Set the graph
with `WithGraphUUID` or `WithGraphUUIDResolver`. The tool supports these
scopes:

- `SearchScopeEdges`
- `SearchScopeNodes`
- `SearchScopeEpisodes`
- `SearchScopeObservations`
- `SearchScopeThreadSummaries`
- `SearchScopeAuto`

The `auto` scope uses `Graph.GetContext`. Other scopes use the matching
scope-specific V4 search method. Search results become text facts for the
model.

The tool exposes search parameters to the model unless an option pins or hides
them. Use `WithToolSearchScope`, `WithToolSearchLimit`, `WithToolReranker`,
`WithToolMMRLambda`, and `WithToolCenterNodeUUID` to pin values. Use
`WithHiddenParams` to hide parameters.

Use `WithToolSearchFilters` for generic V4 filters. Use one typed option when
the scope requires a typed filter:

- `WithToolEdgeSearchFilters`
- `WithToolNodeSearchFilters`
- `WithToolEpisodeSearchFilters`
- `WithToolObservationSearchFilters`
- `WithToolThreadSummarySearchFilters`

`WithToolBFSOriginNodeUUIDs` sets constructor-only breadth-first search origins.

## Memory service

`NewMemoryService` implements ADK's `memory.Service`. Set its graph with
`WithMemoryGraphUUID` or `WithMemoryGraphUUIDResolver`. Set its scope with
`WithSearchScope`, and set a result limit with `WithSearchLimit`.
`AddSessionToMemory` is a no-op because the callbacks already persist each
turn. A search failure is logged and returns an empty result.

## Setup and tests

See [`SETUP.md`](SETUP.md) for API key setup and local commands.

Run unit tests with:

```bash
go test ./...
```

Run live tests with `ZEP_API_KEY` set:

```bash
ZEP_API_KEY=... go test -run TestLive -v ./...
```
