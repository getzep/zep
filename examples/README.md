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

The Python and TypeScript ontology examples replace project-level ontology
configuration. Run them with a disposable project key. The Go ontology
example replaces only the ontology for the user graph in
`ZEP_ENTITY_TYPES_USER_UUID`.
