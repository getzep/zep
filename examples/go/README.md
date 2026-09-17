# Zep Go examples (graph / ontology)

Runnable snippets for the published [`github.com/getzep/zep-go/v4`](https://pkg.go.dev/github.com/getzep/zep-go/v4) SDK.

The packaged app under [`chunking-example/`](./chunking-example) keeps its own
module and is not part of this one.

## Setup

```bash
cd examples/go
go mod download
export ZEP_API_KEY=your_key
```

## Run

| Command | What it runs |
| --- | --- |
| `go run .` | User graph via thread messages plus graph search (`user_graph.go` and `conversations.go`) |
| `go run . entity-types` | Register custom entity and edge types |

`go run . entity-types` calls `Project.SetOntology`, which replaces the
ontology for the whole project. Use a disposable project key. Set
`ZEP_ENTITY_TYPES_GRAPH_UUID` to also search a graph for the new types.
