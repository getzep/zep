# Zep Google ADK (Go) Integration

`zepadk` gives [Google Agent Development Kit (ADK) for Go](https://github.com/google/adk-go)
agents persistent, cross-session memory backed by [Zep](https://www.getzep.com),
Zep's temporal Context Graph platform for agent memory.

It plugs into ADK's native extension points — it does **not** wrap or replace the
ADK runtime:

- **Context injection** via an `llmagent.BeforeModelCallback` that persists each
  new user turn to Zep and injects the user's Context Block into the model
  request. It skips tool-loop continuations so a turn is recorded exactly once.
- **Assistant persistence** via an `llmagent.AfterModelCallback` that writes the
  assistant's reply back to the same Zep thread, so the user graph captures both
  sides of the conversation.
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

Requirements: Go 1.25+ (`google.golang.org/adk` v1.4.0 requires Go 1.25),
`google.golang.org/adk` v1.4.0, `github.com/getzep/zep-go/v4` v4.0.0-alpha.5.

## Identifiers in Zep v4

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
user ID or a thread ID is a name, not an address.

The application creates the Zep user and thread one time with `CreateUser` and
`CreateThread`, stores the returned UUIDs in its own database, and gives them
to this package on each turn. The package never resolves a name at run time,
because a lookup on every turn adds a round-trip to the request path.

## Choosing a component

zep-adk ships the same set of capabilities across Python, TypeScript, and Go, though the exact symbol names differ per language's ADK idioms:

| Capability | Python | TypeScript | Go |
|---|---|---|---|
| guaranteed context injection | `ZepContextTool` | `ZepContextTool` or `createZepBeforeModelCallback` | `NewBeforeModelCallback` |
| assistant-turn persistence | `create_after_model_callback` | `createZepAfterModelCallback` | `NewAfterModelCallback` |
| explicit creation that returns UUIDs | `create_user`/`create_thread` | `createUser`/`createThread` | `CreateUser`/`CreateThread` |
| custom context block | `context_builder` | `contextBuilder` | `WithContextBuilder` |
| injection template | `context_template` | `contextTemplate` | `WithContextTemplate` |
| model-callable graph search (pin-or-expose, 6 scopes) | `ZepGraphSearchTool` | `ZepGraphSearchTool` | `NewGraphSearchTool` |
| ADK-native memory service | `ZepMemoryService` | `ZepMemoryService` | `NewMemoryService` |

Note: Go intentionally has no tool-based injection (callbacks are the Go-ADK-idiomatic hook).

Note: `CreateUser` takes positional `userID`, `firstName`, `lastName`, and `email` strings (pass `""` to omit). It returns the UUID of the user and the UUID of the graph of the user.

## Quick start

```go
zep := zepadk.NewClientFromEnv() // nil when ZEP_API_KEY is unset -> no-op

// Create the Zep user and thread out of band, before the first turn -- for
// example during account or session onboarding. Store the UUIDs in your own
// database. Each call creates a new resource, so do not call it on every
// session start.
userUUID, graphUUID, _ := zepadk.CreateUser(ctx, zep, userID, "Jane", "Smith", "jane@example.com")
threadUUID, _ := zepadk.CreateThread(ctx, zep, sessionID, userUUID)

searchTool, _ := zepadk.NewGraphSearchTool(zep, zepadk.WithGraphUUID(graphUUID))

agent, _ := llmagent.New(llmagent.Config{
    Name:  "assistant",
    Model: llm, // a model.LLM, for example gemini.NewModel(...)
    BeforeModelCallbacks: []llmagent.BeforeModelCallback{
        zepadk.NewBeforeModelCallback(zep,
            zepadk.WithThreadUUID(threadUUID),
            zepadk.WithUserUUID(userUUID)),
    },
    AfterModelCallbacks: []llmagent.AfterModelCallback{
        zepadk.NewAfterModelCallback(zep, zepadk.WithAfterThreadUUID(threadUUID)),
    },
    Tools: []tool.Tool{searchTool},
})

run, _ := runner.New(runner.Config{
    AppName:        "my_app",
    Agent:          agent,
    SessionService: sessions,
    MemoryService:  zepadk.NewMemoryService(zep, zepadk.WithMemoryGraphUUID(graphUUID)),
})
```

An application that serves many users passes a resolver instead of a fixed
UUID: `WithThreadUUIDResolver`, `WithUserUUIDResolver`,
`WithAfterThreadUUIDResolver`, `WithGraphUUIDResolver`, and
`WithMemoryGraphUUIDResolver`. Each resolver reads the UUID from the store of
the application with the ADK session ID or the ADK user ID as the key.

See [`examples/main.go`](examples/main.go) for a complete, runnable wiring of
the agent, runner, session service, and Zep user and thread creation.

## How it works

The application maps its own ADK identifiers to Zep UUIDs:

| ADK | Zep |
|-----|-----|
| session ID | thread UUID, from `CreateThread` |
| user ID | user UUID and graph UUID, from `CreateUser` |

Create the Zep user and thread out of band before the first turn with
`CreateUser` and `CreateThread`, and store the UUIDs. Then, on a genuinely new
user turn, the callback returned by `NewBeforeModelCallback`:

1. Reads the user's latest message from the ADK callback context.
2. Truncates it to Zep's 4,096-character per-message limit if needed (logging a
   lengths-only warning — message content is never dropped or logged).
3. Persists it to the user's Zep thread and retrieves the context to inject —
   either via a single `Thread.AddMessages` round-trip with
   `ReturnContext=true` (the default), or, when `WithContextBuilder` is
   configured, by persisting (`Thread.AddMessages` without `ReturnContext`) and
   running the custom builder concurrently (see below).
4. Injects the resulting context block into `req.Config.SystemInstruction`,
   rendered through the configured template (see `WithContextTemplate`).

During a tool loop ADK re-invokes the before-model callback after each tool
result. On those continuations the latest content in `req.Contents` is a function
response rather than new user input, so the callback returns early without
re-persisting the message or re-injecting the Context Block — a turn that calls
`search_memory` is recorded in Zep exactly once.

`NewAfterModelCallback` complements it: after the model replies, it persists the
assistant's text to the same thread (as an `assistant` message). It skips model
errors, partial streaming chunks, and function-call-only responses (tool-loop
steps), so only genuine replies are recorded.

### Custom context builders and templates

By default, the before-model callback injects the Zep Context Block returned by
`Thread.AddMessages(ReturnContext=true)`, wrapped in `DefaultContextTemplate`.
Two options let you customize this:

`WithContextBuilder` replaces context retrieval with your own function — for
example a filtered graph search, a multi-graph query, or a template pulled from
elsewhere. When set, message persistence and the builder run **concurrently**
for lower latency, and each is isolated from the other's failure: a builder
error only skips injection (persistence still completes); a persist failure
does not prevent a successful builder result from being injected.

```go
builder := func(ctx context.Context, in zepadk.ContextInput) (string, error) {
    page, err := in.Client.Graph.SearchEdges(ctx, graphUUID, &zep.GraphSearchEdgesRequest{
        Limit: zep.Int(10),
        Body:  &zep.SearchRequest{Query: in.UserMessage},
    })
    if err != nil {
        return "", err
    }
    var facts []string
    for _, edge := range page.Results {
        if edge.Fact != nil {
            facts = append(facts, *edge.Fact)
        }
    }
    return strings.Join(facts, "\n"), nil
}

before := zepadk.NewBeforeModelCallback(zep,
    zepadk.WithThreadUUID(threadUUID),
    zepadk.WithUserUUID(userUUID),
    zepadk.WithContextBuilder(builder))
```

`WithContextTemplate` overrides how the retrieved (or built) context block is
wrapped before injection. The template must contain a literal `{context}`
placeholder, substituted via `strings.ReplaceAll` (never `fmt` verbs), so
content containing `%`, `{`, or `}` is always safe:

```go
before := zepadk.NewBeforeModelCallback(zep,
    zepadk.WithContextTemplate("Known facts about the user:\n{context}"))
```

`WithContextPrefix` is deprecated in favor of `WithContextTemplate`; it remains
as a compiling shim (`prefix + "{context}"`, i.e. no `<ZEP_CONTEXT>` wrapper) for
existing callers.

The public surface lives in:

- [`zepadk.go`](zepadk.go) — `NewBeforeModelCallback`, `NewAfterModelCallback`,
  `CreateUser`, `CreateThread`, `ContextInput`, `ContextBuilder`, and the
  helpers `InjectSystemInstruction`, `LastUserText`, `AssistantText`,
  `IsToolLoopContinuation`.
- [`memory.go`](memory.go) — `NewMemoryService` (the ADK `memory.Service` over
  the Zep graph search methods).
- [`tool.go`](tool.go) — `NewGraphSearchTool` (the on-demand `search_memory` tool).
- [`search.go`](search.go) — scope-aware mapping of Zep search results.
- [`client.go`](client.go) — `NewClient` / `NewClientFromEnv`.

### Search scopes

The memory service and search tool map every supported Zep search scope into
results — earlier versions read only `edges` and silently returned nothing for
other scopes:

| Scope | Result |
|-------|--------|
| `edges` (default) | facts |
| `nodes` | entity summaries (`name: summary`) |
| `episodes` | message/data content |
| `observations` | derived memories |
| `thread_summaries` | incremental thread summaries |
| `auto` | the pre-materialized Context Block |

Zep v4 has one search method for each scope, and it exports no scope enum, so
this package defines the `zepadk.SearchScope` values in the table above. An
unsupported scope value is rejected loudly: the service or tool logs an error and
returns no results rather than silently swallowing them.

### Graph search tool: pin-or-expose parameters

`NewGraphSearchTool` builds an explicit JSON schema for the parameters the
model can supply. Every search parameter — `scope`, `reranker`, `limit`,
`mmr_lambda`, `center_node_uuid` — is independently in one of three states:

| State | How | Effect |
|-------|-----|--------|
| **Exposed** (default) | no option passed | Parameter appears in the model's tool schema with the documented default; the model may choose a value. |
| **Pinned** | `WithToolSearchScope`, `WithToolSearchLimit`, `WithToolReranker`, `WithToolMMRLambda`, `WithToolCenterNodeUUID` | Hidden from the schema; always sent to Zep with the fixed value, regardless of what the model would have chosen. |
| **Hidden** | `WithHiddenParams(zepadk.SearchParamScope, ...)` | Hidden from the schema AND omitted from the Zep call entirely — as if never set. |

Defaults when exposed: `scope="edges"`, `reranker="rrf"`, `limit=10`;
`mmr_lambda` and `center_node_uuid` have no default (omitted unless the model
supplies one). An invalid enum value from the model (`scope`/`reranker`) is
rejected by ADK's schema validation before the tool runs and surfaced to the
model as a tool error it can correct on the next call; it never reaches Zep
and never crashes the host agent.

`WithToolSearchFilters` and `WithToolBFSOriginNodeUUIDs` are constructor-only:
never exposed to the model, always applied to every search when set.

```go
// Fully open: the model chooses scope, reranker, limit, mmr_lambda, and
// center_node_uuid for every call.
tool, _ := zepadk.NewGraphSearchTool(zep, zepadk.WithGraphUUID(graphUUID))

// Pin scope and limit; leave reranker/mmr_lambda/center_node_uuid exposed.
tool, _ := zepadk.NewGraphSearchTool(zep,
    zepadk.WithGraphUUID(graphUUID),
    zepadk.WithToolSearchScope(zepadk.SearchScopeNodes),
    zepadk.WithToolSearchLimit(5),
)

// Hide mmr_lambda and center_node_uuid without pinning them to a value
// (useful when the reranker is never "mmr" or "node_distance").
tool, _ := zepadk.NewGraphSearchTool(zep,
    zepadk.WithGraphUUID(graphUUID),
    zepadk.WithHiddenParams(zepadk.SearchParamMMRLambda, zepadk.SearchParamCenterNodeUUID),
)
```

**Behavior change (migration):** prior versions of `WithToolSearchScope` and
`WithToolSearchLimit` configured a hidden default scope/limit for every
search — the model never saw or controlled them. As of this version, an
absent option **exposes** that parameter to the model instead. To restore the
old always-pinned behavior, pin every parameter explicitly:

```go
tool, _ := zepadk.NewGraphSearchTool(zep,
    zepadk.WithToolSearchScope(zepadk.SearchScopeEdges),
    zepadk.WithToolSearchLimit(10),
    zepadk.WithHiddenParams(
        zepadk.SearchParamReranker,
        zepadk.SearchParamMMRLambda,
        zepadk.SearchParamCenterNodeUUID,
    ),
)
```

## Configuration

Each constructor accepts functional options:

| Constructor | Options |
|-------------|---------|
| `NewBeforeModelCallback` | `WithThreadUUID`, `WithThreadUUIDResolver`, `WithUserUUID`, `WithUserUUIDResolver`, `WithContextBuilder`, `WithContextTemplate`, `WithContextPrefix` (deprecated), `WithUserMessageName`, `WithLogger` |
| `NewAfterModelCallback` | `WithAfterThreadUUID`, `WithAfterThreadUUIDResolver`, `WithAssistantMessageName`, `WithAfterLogger` |
| `NewMemoryService` | `WithMemoryGraphUUID`, `WithMemoryGraphUUIDResolver`, `WithSearchScope`, `WithSearchLimit`, `WithMemoryLogger` |
| `NewGraphSearchTool` | `WithToolName`, `WithToolDescription`, `WithGraphUUID`, `WithGraphUUIDResolver`, `WithToolSearchScope`, `WithToolSearchLimit`, `WithToolReranker`, `WithToolMMRLambda`, `WithToolCenterNodeUUID`, `WithToolSearchFilters`, `WithToolBFSOriginNodeUUIDs`, `WithHiddenParams`, `WithToolLogger` |

`WithGraphUUID` selects the graph that the search tool reads. The UUID of the
graph of a user is the second value that `CreateUser` returns. A standalone
graph has its own UUID. Without a graph UUID the tool returns no facts and
logs an error.

When a pinning option (e.g. `WithToolSearchScope`) and `WithHiddenParams` target
the same parameter, whichever is passed later to `NewGraphSearchTool` wins —
options are applied in call order, last write wins.

## Error handling

A Zep failure never crashes the host agent:

- A `nil` client (for example when `ZEP_API_KEY` is unset) makes the callback,
  memory service, and tool safe no-ops. A missing thread UUID or graph UUID
  has the same effect for the affected component.
- Transient Zep errors are logged via the configured `slog.Logger` and swallowed;
  the callback proceeds to the model without injected memory, and the memory
  service and tool return empty results.

## Notes

- **Ingestion is asynchronous.** A message added during a turn is not guaranteed
  to be retrievable within that same turn; the returned Context Block reflects
  prior turns. Design for eventual availability.
- **Reuse one client** across the lifetime of the process.
- Pass real user names (and ideally a last name and an email) to `CreateUser`,
  so that Zep resolves the identity of the user in the graph.

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
