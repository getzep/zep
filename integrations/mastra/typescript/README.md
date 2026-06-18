# Zep Mastra Integration

`@getzep/zep-mastra` adds [Zep](https://www.getzep.com) long-term memory to
[Mastra](https://mastra.ai) agents. It exposes Zep's temporal Context Graph as a
small set of idiomatic Mastra tools your agent can call to **persist**,
**search**, and **recall** user context across turns and sessions.

## Why tools

Zep is a temporal knowledge graph, not a row-oriented message store. Rather than
forcing Zep through a `MastraStorage` adapter (which would require CRUD methods
the graph model can't honor faithfully), this package exposes Zep's two real
operations — persist and retrieve — as `createTool` tools that drop straight into
a Mastra `Agent`'s `tools` record.

## Installation

```bash
npm install @getzep/zep-mastra @getzep/zep-cloud @mastra/core
```

`@mastra/core` is a peer dependency. See [SETUP.md](./SETUP.md) for how to sign
up for Zep and create an API key.

## Quick start

```ts
import { ZepClient } from "@getzep/zep-cloud";
import { Agent } from "@mastra/core/agent";
import { createZepToolset, ensureZepUserAndThread } from "@getzep/zep-mastra";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// 1. Provision the Zep user + thread before the first turn.
const binding = { userId: "user-123", threadId: "thread-abc" };
await ensureZepUserAndThread({ client, ...binding, firstName: "Jane", lastName: "Smith" });

// 2. Build the tool set bound to that user + thread.
const { zepRemember, zepSearch, zepContext } = createZepToolset({ client, binding });

// 3. Attach the tools to an Agent (id AND name are both required).
const agent = new Agent({
  id: "memory-agent",
  name: "Memory Agent",
  instructions: "You have long-term memory. Store and recall user facts.",
  model: "openai/gpt-4o-mini",
  tools: { zepRemember, zepSearch, zepContext },
});
```

A complete, runnable example is in [`examples/basic-agent.ts`](./examples/basic-agent.ts)
(`npm run example`).

## The tools

| Tool key | Zep operation | What it does |
|----------|---------------|--------------|
| `zepRemember` | `thread.addMessages` / `graph.add` | Persists a message or fact. Conversational content (a `role` + a bound thread) is recorded via `thread.addMessages`; everything else is ingested via `graph.add`. See [`src/remember-tool.ts`](./src/remember-tool.ts). |
| `zepSearch` | `graph.search` | Model-callable search over the bound graph; returns relevant facts. Scope/limit/reranker are pinned at construction. See [`src/search-tool.ts`](./src/search-tool.ts). |
| `zepContext` | `thread.getUserContext` | Returns the prompt-ready Context Block assembled from the *whole* user graph. See [`src/context-tool.ts`](./src/context-tool.ts). |

Each tool is also exported as a standalone factory (`createZepRememberTool`,
`createZepSearchTool`, `createZepContextTool`) for when you want to wire one tool
with custom options. `createZepToolset` and `ensureZepUserAndThread` live in
[`src/toolset.ts`](./src/toolset.ts).

## Binding: user graph vs standalone graph

Tools are bound to a graph via a `ZepBinding`:

- **`userId`** targets a **user graph** — the home for personalized agent memory.
  Use this for a conversational agent that remembers an end user. The `zepContext`
  tool requires a `threadId` too (the thread scopes relevance; retrieval still
  spans the whole user graph).
- **`graphId`** targets a **standalone graph** — shared or domain knowledge (a
  product knowledge base, runbooks). No user node, no user summary.

If both are set, `userId` wins. If neither is set, tools return a graceful "not
configured" result instead of throwing.

## Roles

`zepRemember` accepts an arbitrary `role` string and maps it onto Zep's closed
`RoleType` enum (`user | assistant | system | tool | function | norole`), so
host-framework role names like `human` or `ai` are coerced safely; unknown roles
fall back to `norole`. The mapper is exported as `toRoleType`.

## Error handling

Every tool handles Zep failures gracefully: a failure is logged through the
configured logger (default `console`) and surfaced to the model as a
`stored: false` / `found: false` result. **A Zep outage never throws and never
crashes the host agent.** Pass a custom `logger` to integrate with your logging
stack.

## Ingestion is asynchronous

Zep builds the graph asynchronously — a fact you just stored is not instantly
retrievable. Design flows for eventual availability; don't read-after-write
within a single turn. The example waits before recalling.

## Development

```bash
npm install
npm run typecheck   # tsc --noEmit (NodeNext + strict)
npm test            # vitest (mock-based; live tests gated on ZEP_API_KEY)
npm run build       # tsup → dist (ESM + CJS + d.ts)
```

## Requirements

- Node.js >= 20
- `@getzep/zep-cloud` >= 3.23.0 (Zep V3)
- `@mastra/core` >= 1.42.0 (peer)

## Links

- [Zep documentation](https://help.getzep.com)
- [Mastra documentation](https://mastra.ai/docs)
- [GitHub issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](./LICENSE).
