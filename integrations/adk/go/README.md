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
`google.golang.org/adk` v1.4.0, `github.com/getzep/zep-go/v3` v3.23.0.

## Choosing a component

zep-adk ships the same set of capabilities across Python, TypeScript, and Go, though the exact symbol names differ per language's ADK idioms:

| Capability | Python | TypeScript | Go |
|---|---|---|---|
| guaranteed context injection | `ZepContextTool` | `ZepContextTool` or `createZepBeforeModelCallback` | `NewBeforeModelCallback` |
| assistant-turn persistence | `create_after_model_callback` | `createZepAfterModelCallback` | `NewAfterModelCallback` |
| explicit provisioning + created signal | `ensure_user`/`ensure_thread` | `ensureUser`/`ensureThread` | `EnsureUser`/`EnsureThread` |
| custom context block | `context_builder` | `contextBuilder` | `WithContextBuilder` |
| injection template | `context_template` | `contextTemplate` | `WithContextTemplate` |
| model-callable graph search (pin-or-expose, 6 scopes) | `ZepGraphSearchTool` | `ZepGraphSearchTool` | `NewGraphSearchTool` |
| ADK-native memory service | `ZepMemoryService` | `ZepMemoryService` | `NewMemoryService` |

Note: Go intentionally has no tool-based injection (callbacks are the Go-ADK-idiomatic hook).

Note: Go has no `onCreated`/`on_created` hook -- use the `created` bool returned by `EnsureUser`/`EnsureThread` instead. `EnsureUser` takes positional `firstName`, `lastName`, `email` strings (pass `""` to omit).

## Quick start

```go
zep := zepadk.NewClientFromEnv() // nil when ZEP_API_KEY is unset -> no-op

// Provision the Zep user and thread out of band, before the first turn --
// e.g. during account/session onboarding. Both calls are idempotent.
created, _ := zepadk.EnsureUser(ctx, zep, userID, "Jane", "Smith", "jane@example.com")
if created {
    // One-time per-user setup goes here (ontology, custom instructions, etc.).
}
zepadk.EnsureThread(ctx, zep, sessionID, userID)

agent, _ := llmagent.New(llmagent.Config{
    Name:                 "assistant",
    Model:                llm, // a model.LLM, e.g. gemini.NewModel(...)
    BeforeModelCallbacks: []llmagent.BeforeModelCallback{zepadk.NewBeforeModelCallback(zep)},
    AfterModelCallbacks:  []llmagent.AfterModelCallback{zepadk.NewAfterModelCallback(zep)},
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
`EnsureUser` and `EnsureThread` (both idempotent). Then, on a genuinely new user
turn, the callback returned by `NewBeforeModelCallback`:

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
    results, err := in.Client.Graph.Search(ctx, &zep.GraphSearchQuery{
        UserID: zep.String(in.UserID),
        Query:  in.UserMessage,
        Scope:  zep.GraphSearchScopeEdges.Ptr(),
    })
    if err != nil {
        return "", err
    }
    var facts []string
    for _, e := range results.Edges {
        facts = append(facts, e.Fact)
    }
    return strings.Join(facts, "\n"), nil
}

before := zepadk.NewBeforeModelCallback(zep, zepadk.WithContextBuilder(builder))
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
  `EnsureUser`, `EnsureThread`, `ContextInput`, `ContextBuilder`, and the
  helpers `InjectSystemInstruction`, `LastUserText`, `AssistantText`,
  `IsToolLoopContinuation`.
- [`memory.go`](memory.go) — `NewMemoryService` (the ADK `memory.Service` over
  Zep `Graph.Search`).
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
| `thread_summaries` | incremental thread summaries (`name: summary`) |
| `auto` | the pre-materialized Context Block |

An unsupported scope (a future value the Zep SDK adds before this package's
mapping is updated) is rejected loudly: the service or tool logs an error and
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
tool, _ := zepadk.NewGraphSearchTool(zep)

// Pin scope and limit; leave reranker/mmr_lambda/center_node_uuid exposed.
tool, _ := zepadk.NewGraphSearchTool(zep,
    zepadk.WithToolSearchScope(zep.GraphSearchScopeNodes),
    zepadk.WithToolSearchLimit(5),
)

// Hide mmr_lambda and center_node_uuid without pinning them to a value
// (useful when the reranker is never "mmr" or "node_distance").
tool, _ := zepadk.NewGraphSearchTool(zep,
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
    zepadk.WithToolSearchScope(zep.GraphSearchScopeEdges),
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
| `NewBeforeModelCallback` | `WithContextBuilder`, `WithContextTemplate`, `WithContextPrefix` (deprecated), `WithUserMessageName`, `WithLogger` |
| `NewAfterModelCallback` | `WithAssistantMessageName`, `WithAfterLogger` |
| `NewMemoryService` | `WithSearchScope`, `WithSearchLimit`, `WithMemoryLogger` |
| `NewGraphSearchTool` | `WithToolName`, `WithToolDescription`, `WithGraphID`, `WithToolSearchScope`, `WithToolSearchLimit`, `WithToolReranker`, `WithToolMMRLambda`, `WithToolCenterNodeUUID`, `WithToolSearchFilters`, `WithToolBFSOriginNodeUUIDs`, `WithHiddenParams`, `WithToolLogger` |

`WithGraphID` scopes the search tool to a standalone graph instead of the calling
user's graph (`UserID` and `GraphID` are mutually exclusive in Zep).

When a pinning option (e.g. `WithToolSearchScope`) and `WithHiddenParams` target
the same parameter, whichever is passed later to `NewGraphSearchTool` wins —
options are applied in call order, last write wins.

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
