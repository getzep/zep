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
import { createZepProcessors, createZepUserAndThread } from "@getzep/zep-mastra";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// 1. Create the Zep user + thread before the first turn. Zep generates the
//    UUIDs. Store them in your own database and reuse them on every turn.
const identity = await createZepUserAndThread({
  client,
  firstName: "Jane",
  lastName: "Smith",
});
if (!identity) throw new Error("Could not create the Zep user and thread.");

// 2. Build the processor pair bound to that graph + thread.
const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  graphUuid: identity.graphUuid,
  threadUuid: identity.threadUuid,
});

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
   Zep Context Block (`thread.getContext`, or a custom `contextBuilder`), wraps it with
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

Every Zep call is wrapped: a missing `threadUuid` or any Zep failure degrades gracefully
(messages pass through unchanged, a warning is logged) and **never** calls `abort()` or
throws into the agent loop.

### Customizing context injection

```ts
import { DEFAULT_CONTEXT_TEMPLATE, createZepProcessors } from "@getzep/zep-mastra";

const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  graphUuid,
  threadUuid,
  // Replace thread.getContext with your own retrieval:
  contextBuilder: async ({ client, graphUuid, userMessage }) => {
    if (!graphUuid) return undefined;
    const result = await client.graph.searchEdges(graphUuid, { body: { query: userMessage } });
    return result.data.map((e) => e.fact).join("\n");
  },
  // Or just customize the wrapping template (must contain a literal `{context}`):
  contextTemplate: "Known facts about the user:\n{context}",
  // Or fully take over formatting (wins over contextTemplate):
  formatContext: (context) => `<memory>${context}</memory>`,
});
```

### Per-call identity

Pass `resolveIdentity` to resolve `graphUuid`/`threadUuid` per call from Mastra's
`requestContext`, instead of binding a fixed identity at construction time — useful when a
single processor instance serves many end users:

```ts
const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  resolveIdentity: (requestContext) => ({
    graphUuid: (requestContext as { graphUuid?: string } | undefined)?.graphUuid,
    threadUuid: (requestContext as { threadUuid?: string } | undefined)?.threadUuid,
  }),
});
```

The same `resolveIdentity` option is accepted by `createZepSearchTool`,
`createZepRememberTool`, and `createZepContextTool` (resolved from each tool call's
`context.requestContext`).

## Provisioning: `createZepUserAndThread`

Zep requires the user and thread to exist before messages are added. Call
`createZepUserAndThread` once, out-of-band, before the first turn, then store the
returned UUIDs in your own database. Zep v4 addresses every later call by UUID, so
this function is **not** idempotent on a name: a second call creates a second user.

```ts
const identity = await createZepUserAndThread({
  client,
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
  // Runs exactly once, immediately after the user is created —
  // e.g. configure per-user summary instructions:
  onUserCreated: async (client, userUuid) => {
    await client.user.setSummaryInstructions(userUuid, {
      instructions: [{ name: "diet", text: "Track the user's dietary preferences." }],
    });
  },
});
// identity: { userUuid, graphUuid, threadUuid } | null
```

A failure (auth, network, 5xx) is logged at `warn` and reported as `null` rather than
thrown, so a Zep outage never crashes the caller.

`createZepUserAndThread` also accepts optional `userId` and `threadId` names. Zep does
not use them as addresses. Supply a `userId` if you want a human-readable name in the
Zep application.

## Tools

The pre-0.2.0 tool-only surface is still available and fully supported — use it when you
want the model itself to decide when to persist or recall, or alongside the processors.

```ts
import { createZepToolset, createZepUserAndThread } from "@getzep/zep-mastra";

const identity = await createZepUserAndThread({ client, firstName: "Jane", lastName: "Smith" });
if (!identity) throw new Error("Could not create the Zep user and thread.");

const binding = { graphUuid: identity.graphUuid, threadUuid: identity.threadUuid };
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
| `zepRemember` | `thread.addMessages` / `graph.episode.add` | Persists a message or fact. Conversational content (a `role` + a bound thread) is recorded via `thread.addMessages`; everything else is ingested via `graph.episode.add`. See [`src/remember-tool.ts`](./src/remember-tool.ts). |
| `zepSearch` | `graph.searchEdges` and the other scope methods | Model-callable search over the bound graph; returns relevant facts. See "Pin-or-expose search" below. See [`src/search-tool.ts`](./src/search-tool.ts). |
| `zepContext` | `thread.getContext` | Returns the prompt-ready Context Block assembled from the *whole* user graph. See [`src/context-tool.ts`](./src/context-tool.ts). |

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
  binding: { graphUuid },
  pinnedParams: { scope: "edges", reranker: "rrf", limit: 10 },
});

// Hide mmrLambda/centerNodeUuid from the schema without fixing a value.
createZepSearchTool({
  client,
  binding: { graphUuid },
  hiddenParams: new Set(["mmrLambda", "centerNodeUuid"]),
});
```

`filters` and `bfsOriginNodeUuids` are always constructor-only — never exposed to the
model — and applied whenever set.

## Binding: user graph vs standalone graph

Zep v4 addresses every graph by its server-generated UUID, so a user graph and a
standalone graph are the same kind of address. Bind the tools and the processors with
`graphUuid`, and bind the thread-scoped surfaces with `threadUuid`:

- The `graphUuid` of a **user graph** is the `graphUuid` field of the `User` that
  `user.create` returns. A user graph is the home for personalized agent memory.
- The `graphUuid` of a **standalone graph** is the `uuid` field of the `Graph` that
  `graph.create` returns. A standalone graph holds shared or domain knowledge, such as
  a product knowledge base. It has no user node and no user summary.
- The `threadUuid` is the `uuid` field of the `Thread` that `thread.create` returns.
  Context retrieval and the `zepContext` tool require it. The thread scopes relevance;
  retrieval still spans the whole user graph.

Resolve a v3 `userId` or `graphId` name to its UUID one time, then store the UUID in
your own database. The integration never calls `lookup` at run time. If no `graphUuid`
or no `threadUuid` is available, the tools and the processors degrade gracefully
instead of throwing.

## Roles

`zepRemember` accepts an arbitrary `role` string and maps it onto Zep's closed
`RoleType` enum (`user | assistant | system | tool | function`), so host-framework
role names like `human` or `ai` are coerced safely; an unknown role is omitted. The
mapper is exported as `toRoleType`.

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
- `@getzep/zep-cloud` 4.0.0-alpha.5 (Zep v4)
- `@mastra/core` >= 1.42.0 (peer)

## Links

- [Zep documentation](https://help.getzep.com)
- [Mastra documentation](https://mastra.ai/docs)
- [GitHub issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](./LICENSE).
