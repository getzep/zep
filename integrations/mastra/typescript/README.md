# Zep Mastra Integration

`@getzep/zep-mastra` adds [Zep](https://www.getzep.com) long-term memory to
[Mastra](https://mastra.ai) agents, on top of Zep's temporal Context Graph.

Two complementary surfaces:

- **Automatic memory (recommended)** — `ZepInputProcessor`/`ZepOutputProcessor`
  plug directly into Mastra's native `inputProcessors`/`outputProcessors` pipeline. No
  tool-calling round-trip: context is injected and turns are persisted on every call,
  automatically.
- **Tools** — `zepRemember`/`zepSearch`/`zepContext` let the model decide when to persist
  or recall. Use these when you want the model in the loop, or alongside the processors.

## Installation

```bash
npm install @getzep/zep-mastra @getzep/zep-cloud @mastra/core
```

`@mastra/core` is a peer dependency. See [SETUP.md](./SETUP.md) for how to sign
up for Zep and create an API key.

## Automatic memory (processors)

`createZepProcessors` builds a bound `{ inputProcessor, outputProcessor }` pair:

```ts
import { ZepClient } from "@getzep/zep-cloud";
import { Agent } from "@mastra/core/agent";
import { createZepProcessors, ensureZepUserAndThread } from "@getzep/zep-mastra";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
const userId = "user-123";
const threadId = "thread-abc";

// 1. Provision the Zep user + thread before the first turn.
await ensureZepUserAndThread({ client, userId, threadId, firstName: "Jane", lastName: "Smith" });

// 2. Build the processor pair bound to that user + thread.
const { inputProcessor, outputProcessor } = createZepProcessors({ client, userId, threadId });

// 3. Attach to a Mastra agent (id AND name are both required).
const agent = new Agent({
  id: "memory-agent",
  name: "Memory Agent",
  instructions: "You have long-term memory about the user. Use it to personalize replies.",
  model: "openai/gpt-5-mini",
  inputProcessors: [inputProcessor],
  outputProcessors: [outputProcessor],
});
```

On every call:

1. **`ZepInputProcessor`** (`processInput`) extracts the latest user message, retrieves a
   Zep Context Block (`thread.getUserContext`, or a custom `contextBuilder`), wraps it with
   `contextTemplate`/`formatContext`, and injects it as a system message — before the model
   is called.
2. **`ZepOutputProcessor`** (`processOutputResult`) persists the completed turn (the latest
   user message + the assistant's response) to the bound thread via a single
   `thread.addMessages` call — after the model responds. The assistant text is the final
   step's text; when the generation ends mid-tool-loop (`finishReason === "tool-calls"`)
   the user message is still persisted.

Because the input and output processors sit on **opposite sides of the model call**,
running both together is naturally concurrency-safe — the same guarantee ADK's
`beforeModelCallback`/`afterModelCallback` pair provides, for free.

Every Zep call is wrapped: a missing `threadId` or any Zep failure degrades gracefully
(messages pass through unchanged, a warning is logged) and **never** calls `abort()` or
throws into the agent loop.

### Customizing context injection

```ts
import { DEFAULT_CONTEXT_TEMPLATE, createZepProcessors } from "@getzep/zep-mastra";

const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  userId,
  threadId,
  // Replace thread.getUserContext with your own retrieval:
  contextBuilder: async ({ client, userId, threadId, userMessage }) => {
    const result = await client.graph.search({ userId, query: userMessage, scope: "edges" });
    return result.edges?.map((e) => e.fact).join("\n");
  },
  // Or just customize the wrapping template (must contain a literal `{context}`):
  contextTemplate: "Known facts about the user:\n{context}",
  // Or fully take over formatting (wins over contextTemplate):
  formatContext: (context) => `<memory>${context}</memory>`,
});
```

### Per-call identity

Pass `resolveIdentity` to resolve `userId`/`threadId` per call from Mastra's
`requestContext`, instead of binding a fixed identity at construction time — useful when a
single processor instance serves many end users:

```ts
const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  resolveIdentity: (requestContext) => ({
    userId: (requestContext as { userId?: string } | undefined)?.userId,
    threadId: (requestContext as { threadId?: string } | undefined)?.threadId,
  }),
});
```

The same `resolveIdentity` option is accepted by `createZepSearchTool`,
`createZepRememberTool`, and `createZepContextTool` (resolved from each tool call's
`context.requestContext`).

## Provisioning: `ensureZepUserAndThread`

Zep requires the user and thread to exist before messages are added. Call
`ensureZepUserAndThread` once, out-of-band, before the first turn — it's
create-then-catch-conflict, so calling it repeatedly for the same user/thread is safe:

```ts
await ensureZepUserAndThread({
  client,
  userId,
  threadId,
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
  // Fires exactly once, only when the user is genuinely newly created —
  // e.g. configure per-user summary instructions:
  onUserCreated: async (client, userId) => {
    await client.user.addUserSummaryInstructions({
      userIds: [userId],
      instructions: [{ name: "diet", text: "Track the user's dietary preferences." }],
    });
  },
});
```

Genuine failures (auth, network, 5xx) are logged at `warn` and reported via a `false`
return — they are never mistaken for an "already exists" conflict, and never thrown, so
this is safe to call at the start of every turn on a hot path.

## Tools

The pre-0.2.0 tool-only surface is still available and fully supported — use it when you
want the model itself to decide when to persist or recall, or alongside the processors.

```ts
import { createZepToolset, ensureZepUserAndThread } from "@getzep/zep-mastra";

const binding = { userId, threadId };
await ensureZepUserAndThread({ client, ...binding, firstName: "Jane", lastName: "Smith" });

const { zepRemember, zepSearch, zepContext } = createZepToolset({ client, binding });

const agent = new Agent({
  id: "memory-agent",
  name: "Memory Agent",
  instructions: "You have long-term memory. Store and recall user facts.",
  model: "openai/gpt-5-mini",
  tools: { zepRemember, zepSearch, zepContext },
});
```

| Tool key | Zep operation | What it does |
|----------|---------------|--------------|
| `zepRemember` | `thread.addMessages` / `graph.add` | Persists a message or fact. Conversational content (a `role` + a bound thread) is recorded via `thread.addMessages`; everything else is ingested via `graph.add`. See [`src/remember-tool.ts`](./src/remember-tool.ts). |
| `zepSearch` | `graph.search` | Model-callable search over the bound graph; returns relevant facts. See "Pin-or-expose search" below. See [`src/search-tool.ts`](./src/search-tool.ts). |
| `zepContext` | `thread.getUserContext` | Returns the prompt-ready Context Block assembled from the *whole* user graph. See [`src/context-tool.ts`](./src/context-tool.ts). |

Each tool is also exported as a standalone factory (`createZepRememberTool`,
`createZepSearchTool`, `createZepContextTool`) for when you want to wire one tool
with custom options.

### Pin-or-expose search

`createZepSearchTool`'s parameters — `scope`, `reranker`, `limit`, `mmrLambda`,
`centerNodeUuid` — are each **exposed to the model by default** (with Zep's documented
defaults: `scope: "edges"`, `reranker: "rrf"`, `limit: 10`), so the model can choose them
per call. Use `pinnedParams` to fix a parameter to a constant value (removed from the
model's schema, always sent); use `hiddenParams` to remove a parameter from the schema
*without* pinning it (omitted from the Zep call entirely — Zep's own server default
applies):

```ts
// Model only ever sees `query`; scope/reranker/limit are fixed.
createZepSearchTool({
  client,
  binding: { userId },
  pinnedParams: { scope: "edges", reranker: "rrf", limit: 10 },
});

// Hide mmrLambda/centerNodeUuid from the schema without fixing a value.
createZepSearchTool({
  client,
  binding: { userId },
  hiddenParams: new Set(["mmrLambda", "centerNodeUuid"]),
});
```

`searchFilters` and the new `bfsOriginNodeUuids` are always constructor-only — never
exposed to the model — and applied whenever set.

#### Migrating to 0.2.0

The legacy `scope`/`reranker`/`limit` constructor args still work and now pin (and hide)
their parameter exactly as before 0.2.0 — no code changes required to keep the old,
fully-pinned behavior. To make that explicit, use `pinnedParams` instead.

## Binding: user graph vs standalone graph

Tools and processors are bound to a graph via `userId`/`graphId` (tools take these on a
`ZepBinding`; the processors take them directly as `userId`/`threadId`):

- **`userId`** targets a **user graph** — the home for personalized agent memory.
  Use this for a conversational agent that remembers an end user. Context retrieval and
  the `zepContext` tool require a `threadId` too (the thread scopes relevance; retrieval
  still spans the whole user graph).
- **`graphId`** targets a **standalone graph** — shared or domain knowledge (a
  product knowledge base, runbooks). No user node, no user summary. (Standalone graphs
  are supported by the tools; the processors are thread-oriented and expect a `userId`.)

If both are set, `userId` wins. If neither is set (or `threadId` can't be resolved),
tools/processors degrade gracefully instead of throwing.

## Roles

`zepRemember` accepts an arbitrary `role` string and maps it onto Zep's closed
`RoleType` enum (`user | assistant | system | tool | function | norole`), so
host-framework role names like `human` or `ai` are coerced safely; unknown roles
fall back to `norole`. The mapper is exported as `toRoleType`.

## Error handling

Every processor and tool handles Zep failures gracefully: a failure is logged through the
configured logger (default `console`) and the turn proceeds — tools surface a
`stored: false` / `found: false` result to the model; processors pass messages through
unchanged / skip persistence. **A Zep outage never throws and never crashes the host
agent, and the input processor never calls `abort()`.** Pass a custom `logger` to
integrate with your logging stack.

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
