# TypeScript Context Graph examples

These scripts show Context Graph operations with the Zep v4 TypeScript SDK. They create graphs and user threads, add and search episodes, inspect graph records, and set a project ontology.

## Requirements

- Node.js 18 or later.
- Shared project packages: `@getzep/zep-cloud@^4.0.0`, `uuid@^11.1.1`, `@types/node@^22.13.10`, `tsx@^4.19.3`, and `typescript@^5.8.2`.
- Environment variable: `ZEP_API_KEY`.

Warning: `entity_type_example.ts` sets the ontology for the whole project; use a disposable project key.

## Run

From `examples/typescript/`, install the packages:

```bash
npm install
```

Run one script at a time:

- `npm run example:graph`
- `npm run example:graph:user`
- `npm run example:graph:entity-types`

## What the example does

- `graph_example.ts` creates a graph, adds text and JSON episodes, lists nodes and edges, and searches graph edges.
- `user_graph_example.ts` creates a user and thread with server-returned UUIDs, adds sample messages, and calls `thread.getContext`.
- The user graph script lists and reads graph records, adds more episodes, and runs searches centered on a node.
- `entity_type_example.ts` defines destination and travel edge types and sets the project ontology.
