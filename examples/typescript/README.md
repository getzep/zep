# Zep TypeScript examples (graph, memory, users)

Loose snippets that exercise the published [`@getzep/zep-cloud`](https://www.npmjs.com/package/@getzep/zep-cloud) v4 API. Packaged apps under this tree (`eve`, `langgraph`, `chunking-example`, `zep-graph-visualization`) keep their own dependencies and are not part of this package.

## Setup

```bash
cd examples/typescript
npm install
export ZEP_API_KEY=your_key   # required for live runs
```

## Scripts

| Script | What it runs |
| --- | --- |
| `npm run typecheck` | `tsc --noEmit` for `graph/`, `memory/`, `users/` |
| `npm run example:graph` | Create a standalone graph, add episodes, search |
| `npm run example:graph:user` | User graph via thread messages + graph search |
| `npm run example:graph:entity-types` | Register custom entity/edge types (project-wide ontology) |
| `npm run example:memory` | Thread messages + `thread.getContext` memory retrieval |
| `npm run example:users` | Create, update, delete, and list users |

## Notes

- Imports use only public package exports (`ZepClient`, `Zep.*` types/errors, ontology helpers such as `entityFields`).
- `example:graph:entity-types` calls `project.setOntology`, which replaces the **project-level** ontology — use a disposable project key.
