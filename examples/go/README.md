# Zep Go examples (graph / ontology)

Runnable snippets for the published [`github.com/getzep/zep-go/v3`](https://pkg.go.dev/github.com/getzep/zep-go/v3) SDK (pinned to **v3.28.x**).

Module path: `github.com/getzep/zep/examples/zep-go-examples` (final segment avoids `go install .` emitting a binary named `go`).

The packaged app under [`chunking-example/`](./chunking-example) keeps its own module and is not part of this package.

## Setup

```bash
cd examples/go
go mod download
export ZEP_API_KEY=your_key   # required for live runs
```

## Run

| Command | What it runs |
| --- | --- |
| `go run .` | User graph via thread messages + graph search (`user_graph.go` + `conversations.go`) |
| `go run . entity-types` | Register custom entity/edge types (project-wide ontology) |

```bash
go run .
go run . entity-types
```

Unknown arguments print usage and exit nonzero.

Install (binary name `zep-go-examples`):

```bash
GOBIN=/tmp/zep-bin go install .
```

## Notes

- Imports use the public `zep-go/v3` module (`client`, `option`, `graph` packages as needed).
- `go run . entity-types` calls `Graph.SetEntityTypes` without user/graph targets, which replaces the **project-level** ontology — use a disposable project key.
- `Thread.GetUserContext` no longer accepts a `Mode` field on `ThreadGetUserContextRequest` in current v3.28 APIs; the user-graph example passes `nil` for the request.
