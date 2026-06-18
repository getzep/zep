# Zep Vercel AI SDK Integration

`@getzep/zep-vercel-ai` adds [Zep](https://www.getzep.com) long-term memory to
the [Vercel AI SDK](https://ai-sdk.dev) (v6). It exposes Zep's temporal Context
Graph through three layers so you can pick the integration point that fits your
call:

| Layer | Export | Use when |
|-------|--------|----------|
| **Middleware** | `createZepMiddleware` | You want the Context Block injected automatically as a system message on each new user turn. Injection only — pair with `createZepOnFinish` to persist. |
| **Helpers** | `getZepContext`, `persistZepTurn`, `createZepOnFinish` | You want explicit control. `createZepOnFinish` persists the whole turn once per turn from `onFinish` (works for both `generateText` and `streamText`). |
| **Tools** | `createZepTools` | You want the model to retrieve/persist on demand inside a tool loop. |

**Inject via middleware, persist via `onFinish`.** The AI SDK tool loop calls
the wrapped model once per step, so persisting from a per-step middleware hook
would fragment a single turn across many writes and record the model's
intermediate tool-call preamble as a real assistant message. `onFinish` fires
exactly once per turn with the final assistant text, so persistence lives there.

All three layers handle Zep failures gracefully: a Zep outage degrades to "no
memory" and **never crashes the host call**. Warnings log lengths and counts
only — never message content or PII.

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
  createZepOnFinish,
  createZepTools,
  ensureZepUserAndThread,
} from "@getzep/zep-vercel-ai";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// 1. Provision the Zep user + thread before the first turn.
await ensureZepUserAndThread({ client, userId: "u1", threadId: "t1", firstName: "Jane" });

// 2. Wrap the model: inject the Context Block on each new user turn (inject-only).
const model = wrapLanguageModel({
  model: openai("gpt-4o-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1" }),
});

// 3. Optionally let the model search/store memory explicitly.
const tools = createZepTools(client, { binding: { userId: "u1", threadId: "t1" } });

// 4. Persist the whole turn once per turn via onFinish.
const prompt = "What do you remember about me?";
const { text } = await generateText({
  model,
  tools,
  stopWhen: stepCountIs(5),
  prompt,
  onFinish: createZepOnFinish({ client, threadId: "t1", user: prompt }),
});
```

A complete, runnable version is in
[`examples/generate-text.ts`](./examples/generate-text.ts) (`npm run example`).

## Streaming (`streamText`)

The same pattern works unchanged for streaming — **inject via middleware,
persist via `onFinish`**. The middleware's `transformParams` runs for `stream`
calls too (injecting on each new user turn), and `onFinish` fires once per turn
with the final assistant text for `streamText` just as it does for
`generateText`.

```ts
import { streamText, wrapLanguageModel } from "ai";
import { openai } from "@ai-sdk/openai";
import { createZepMiddleware, createZepOnFinish } from "@getzep/zep-vercel-ai";

const userInput = "I just adopted a beagle named Cooper.";

const model = wrapLanguageModel({
  model: openai("gpt-4o-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1" }),
});

const result = streamText({
  model,
  prompt: userInput,
  onFinish: createZepOnFinish({ client, threadId: "t1", user: userInput }),
});

for await (const chunk of result.textStream) process.stdout.write(chunk);
```

Prefer to set `system:` yourself instead of using the middleware? Fetch the
block with `getZepContext` and persist with `persistZepTurn` (or
`createZepOnFinish`) directly. See [`examples/stream-text.ts`](./examples/stream-text.ts).

## The layers in detail

### `createZepMiddleware({ client, threadId })`

Returns a Vercel AI SDK `LanguageModelMiddleware` (`specificationVersion: "v3"`)
for `wrapLanguageModel`. **Injection only** — it does not persist.

- `transformParams` fetches the Context Block (`thread.getUserContext`) and
  prepends it as a `system` message to the provider prompt — on both `generate`
  and `stream` calls — **only on a genuine new user turn** (detected by the last
  prompt message being a `user` message). On tool-loop continuation steps (last
  message is a `tool` result or an `assistant` tool call) it injects nothing, so
  the Context Block is fetched at most once per turn, not once per step.

Persist with `createZepOnFinish` (below). Options also include `formatContext`
(customize the injected system text), `templateId` (custom Context Block
layout), and `logger`. Implementation: [`src/middleware.ts`](./src/middleware.ts).

### `createZepOnFinish({ client, threadId, user?, userId?, ... })`

Returns an AI SDK `onFinish` callback that persists the whole turn **once** —
the user's input plus the final assistant text from the event — via
`thread.addMessages`. `onFinish` fires exactly once per turn (after the entire
tool loop completes) for both `generateText` and `streamText`, so this records
exactly one user message and one assistant message per turn and never writes
intermediate tool-call preamble. Supply the user side via `user` (a string, or a
`(event) => string` resolver); the assistant side is taken from `event.text`.

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
| `zepRemember` | `thread.addMessages` / `graph.add` | Persists a message (a `role` + bound thread; capped at Zep's 4,096-char message limit) or a general fact (`graph.add`; capped at Zep's 10,000-char limit). Over-long content is truncated with a lengths-only warning, never dropped. |
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
- `zod` 3 or 4 (peer; `^3.25.0 || ^4.0.0`)
- `@getzep/zep-cloud` >= 3.23.0 (Zep V3)

## Links

- [Zep documentation](https://help.getzep.com)
- [Vercel AI SDK documentation](https://ai-sdk.dev/docs)
- [GitHub issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](./LICENSE).
