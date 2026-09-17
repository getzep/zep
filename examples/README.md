# Zep examples

Runnable examples for the public Zep Cloud SDKs.

| Language | Directory | SDK |
| --- | --- | --- |
| Python | [`python`](./python) | [`zep-cloud`](https://pypi.org/project/zep-cloud/) v4 |
| TypeScript | [`typescript`](./typescript) | [`@getzep/zep-cloud`](https://www.npmjs.com/package/@getzep/zep-cloud) v4 |
| Go | [`go`](./go) | [`github.com/getzep/zep-go/v4`](https://pkg.go.dev/github.com/getzep/zep-go/v4) |

Every example imports the published SDK package, so none of them require a local
SDK checkout.

## Toolchain

- Python 3.10 or newer.
- Node.js 18 or newer, except `typescript/eve`, which needs Node.js 24.
- `typescript/zep-graph-visualization` uses yarn; the other TypeScript examples use npm.
- Go 1.22 or newer.

## API keys

All examples need `ZEP_API_KEY`. Individual examples document any additional
keys they need in their own README or `.env.example`; the common extras are
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `GOOGLE_API_KEY`.

A few examples replace project-level configuration rather than creating
scoped resources. `go run . entity-types` and the equivalent Python and
TypeScript ontology examples overwrite the ontology for the whole project, so
point them at a disposable project key.

## Identifiers

v4 gives every user, thread, and graph a server-generated UUID. The UUID is
the address of the resource. A `user_id` or a `thread_id` is a name only. The
examples create a resource, read the UUID from the response, and use that UUID
in all later calls.
