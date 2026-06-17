# Zep Vercel AI SDK Integration

`@getzep/zep-vercel-ai` adds [Zep](https://www.getzep.com) long-term memory to
the [Vercel AI SDK](https://ai-sdk.dev) (v6). It exposes Zep's temporal Context
Graph through three layers so you can pick the integration point that fits your
call:

| Layer | Export | Use when |
|-------|--------|----------|
| **Middleware** | `createZepMiddleware` | You want context injected automatically on every model call (and, for `generateText`, the turn persisted automatically). |
| **Helpers** | `getZepContext`, `persistZepTurn` | You want explicit control — the required pattern for `streamText` (set `system:` + persist in `onFinish`). |
| **Tools** | `createZepTools` | You want the model to retrieve/persist on demand inside a tool loop. |

All three handle Zep failures gracefully: a Zep outage degrades to "no memory"
and **never crashes the host call**. Warnings log lengths and counts only —
never message content or PII.

## Installation

```bash
npm install @getzep/zep-vercel-ai @getzep/zep-cloud ai zod
```

`ai` (the Vercel AI SDK, v6) and `zod` are peer dependencies. You'll also want a
model provider such as `@ai-sdk/openai`. See [SETUP.md](./SETUP.md) for how to
sign up for Zep and create an API key.

## Quick start (middleware + tools, `generateText`)

```ts
import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { generateText, stepCountIs, wrapLanguageModel } from "ai";
import {
  createZepMiddleware,
  createZepTools,
  ensureZepUserAndThread,
} from "@getzep/zep-vercel-ai";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// 1. Provision the Zep user + thread before the first turn.
await ensureZepUserAndThread({ client, userId: "u1", threadId: "t1", firstName: "Jane" });

// 2. Wrap the model: inject the Context Block on every call + persist the turn.
const model = wrapLanguageModel({
  model: openai("gpt-4o-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1", persist: true }),
});

// 3. Optionally let the model search/store memory explicitly.
const tools = createZepTools(client, { binding: { userId: "u1", threadId: "t1" } });

const { text } = await generateText({
  model,
  tools,
  stopWhen: stepCountIs(5),
  prompt: "What do you remember about me?",
});
```

A complete, runnable version is in
[`examples/generate-text.ts`](./examples/generate-text.ts) (`npm run example`).

## Streaming (`streamText`): persist in `onFinish`

**Important:** the AI SDK only calls a middleware's `wrapGenerate` for
`generateText`, never for `streamText`. So for streaming:

- **Context injection** still works (the middleware does it in `transformParams`,
  or you can set `system:` yourself with `getZepContext`), but
- **Persistence does not run via the middleware** — you must persist the
  completed turn from `onFinish` with `persistZepTurn`.

```ts
import { streamText } from "ai";
import { openai } from "@ai-sdk/openai";
import { getZepContext, persistZepTurn } from "@getzep/zep-vercel-ai";

const userInput = "I just adopted a beagle named Cooper.";
const context = await getZepContext(client, "t1");

const result = streamText({
  model: openai("gpt-4o-mini"),
  system: context ? `Relevant memory:\n${context}` : undefined,
  prompt: userInput,
  onFinish: ({ text }) => {
    // onFinish fires for BOTH streamText and generateText.
    void persistZepTurn(client, "t1", { user: userInput, assistant: text });
  },
});

for await (const chunk of result.textStream) process.stdout.write(chunk);
```

See [`examples/stream-text.ts`](./examples/stream-text.ts).

## The layers in detail

### `createZepMiddleware({ client, threadId, persist? })`

Returns a Vercel AI SDK `LanguageModelMiddleware` (`specificationVersion: "v3"`)
for `wrapLanguageModel`.

- `transformParams` fetches the Context Block (`thread.getUserContext`) and
  prepends it as a `system` message to the provider prompt — on both `generate`
  and `stream` calls.
- When `persist: true`, `wrapGenerate` records the user+assistant turn via
  `thread.addMessages` after a non-streaming `generateText`. (No-op for
  `streamText` — see the streaming section above.)

Options also include `formatContext` (customize the injected system text),
`templateId` (custom Context Block layout), `userName` / `assistantName`
(speaker names on persisted messages), and `logger`. Implementation:
[`src/middleware.ts`](./src/middleware.ts).

### `getZepContext(client, threadId, options?)` and `persistZepTurn(client, threadId, turn, options?)`

Plain async functions, no framework coupling.

- `getZepContext` returns the prompt-ready Context Block string (or `""`).
- `persistZepTurn` writes a `{ user?, assistant? }` turn via
  `thread.addMessages`; pass `{ returnContext: true }` to fold persist +
  retrieval into one round-trip. Over-long content is truncated to Zep's
  4,096-char message limit with a lengths-only warning.

Implementation: [`src/helpers.ts`](./src/helpers.ts).

### `createZepTools(client, { binding, ... })`

Returns `{ zepSearch, zepRemember, zepContext }` built with the AI SDK's `tool()`
and Zod `inputSchema`. Spread them into a `generateText`/`streamText` `tools`
record so the model can decide when to retrieve or persist.

| Tool | Zep operation | What it does |
|------|---------------|--------------|
| `zepSearch` | `graph.search` | Free-text search over the bound graph; returns relevant facts. Scope/limit/reranker/filters are pinned at construction. |
| `zepRemember` | `thread.addMessages` / `graph.add` | Persists a message (a `role` + bound thread) or a general fact (`graph.add`). |
| `zepContext` | `thread.getUserContext` | Returns the whole-user-graph Context Block on demand. |

Each tool is also exported as a standalone factory (`createZepSearchTool`,
`createZepRememberTool`, `createZepContextTool`). Implementation:
[`src/tools.ts`](./src/tools.ts).

## Binding: user graph vs standalone graph

Tools and `createZepTools` are bound to a graph via a `ZepBinding`:

- **`userId`** targets a **user graph** — the home for personalized agent memory.
  Use it for a conversational agent that remembers an end user. `zepContext`
  (and the middleware) also need a `threadId` — the thread scopes relevance;
  retrieval still spans the whole user graph.
- **`graphId`** targets a **standalone graph** — shared or domain knowledge (a
  product knowledge base, runbooks). No user node, no user summary.

If both are set, `userId` wins. If neither is set, tools return a graceful "not
configured" result instead of throwing.

## Roles

`zepRemember` accepts an arbitrary `role` string and maps it onto Zep's closed
`RoleType` enum (`user | assistant | system | tool | function | norole`), so
loose role names like `human` or `ai` are coerced safely; unknown roles fall
back to `norole`. The mapper is exported as `toRoleType`.

## Ingestion is asynchronous

Zep builds the graph asynchronously — a fact you just stored is not instantly
retrievable. Design flows for eventual availability; don't read-after-write
within a single turn. The example waits before recalling.

## Development

```bash
npm install
npm run typecheck   # tsc --noEmit (NodeNext + strict)
npm run lint        # eslint
npm test            # vitest (mock-based; live test gated on ZEP_API_KEY)
npm run build       # tsup → dist (ESM + CJS + d.ts)
```

## Requirements

- Node.js >= 20
- `ai` >= 6 (peer) — the Vercel AI SDK v6 (this package targets the v3
  middleware/provider interfaces; it is **not** compatible with AI SDK v5)
- `zod` >= 3.25 (peer)
- `@getzep/zep-cloud` >= 3.23.0 (Zep V3)

## Links

- [Zep documentation](https://help.getzep.com)
- [Vercel AI SDK documentation](https://ai-sdk.dev/docs)
- [GitHub issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](./LICENSE).
