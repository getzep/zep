# Zep Vercel AI SDK Integration

`@getzep/zep-vercel-ai` adds [Zep](https://www.getzep.com) long-term memory to
the [Vercel AI SDK](https://ai-sdk.dev) (v6). It exposes Zep's temporal Context
Graph through three layers so you can pick the integration point that fits your
call:

| Layer | Export | Use when |
|-------|--------|----------|
| **Middleware** | `createZepMiddleware` | You want the Context Block injected automatically as a system message on each new user turn. Set `persist: true` for a guaranteed persistence loop, or leave it unset and pair with `createZepOnFinish`. |
| **Helpers** | `getZepContext`, `persistZepTurn`, `createZepOnFinish` | You want explicit control. `createZepOnFinish` persists the whole turn once per turn from `onFinish` (works for both `generateText` and `streamText`). |
| **Tools** | `createZepTools` | You want the model to retrieve/persist on demand inside a tool loop. |

**Two ways to persist — pick one.** By default `createZepMiddleware` is
injection-only (`wrapGenerate`/`wrapStream` are `undefined`); pair it with
`createZepOnFinish` on your `generateText`/`streamText` call. Or pass
`persist: true` (or `{ userName, assistantName }`) to `createZepMiddleware` and
it persists the turn itself via `wrapGenerate`/`wrapStream`, once per turn,
fire-and-forget — no `onFinish` wiring needed. **Don't do both** on the same
call: enabling `persist` AND `createZepOnFinish` together double-persists every
turn (two `thread.addMessages` calls, one from each path).

All layers handle Zep failures gracefully: a Zep outage degrades to "no
memory" and **never crashes the host call**. Warnings log lengths and counts
only — never message content or PII.

## Installation

```bash
npm install @getzep/zep-vercel-ai @getzep/zep-cloud ai zod
```

`ai` (the Vercel AI SDK, v6) and `zod` are peer dependencies. You'll also want a
model provider such as `@ai-sdk/openai`. See [SETUP.md](./SETUP.md) for how to
sign up for Zep and create an API key.

## Quick start (middleware with guaranteed persistence + tools, `generateText`)

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

// 2. Wrap the model: inject the Context Block on each new user turn AND
//    guarantee the turn is persisted — no onFinish wiring needed.
const model = wrapLanguageModel({
  model: openai("gpt-5-mini"),
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

**Prefer explicit `onFinish` wiring instead?** Leave `persist` unset (the
middleware stays injection-only) and pair it with `createZepOnFinish`:

```ts
const model = wrapLanguageModel({
  model: openai("gpt-5-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1" }), // injection only
});

const prompt = "What do you remember about me?";
const { text } = await generateText({
  model,
  tools,
  stopWhen: stepCountIs(5),
  prompt,
  onFinish: createZepOnFinish({ client, threadId: "t1", user: prompt }),
});
```

Don't combine `persist: true` with `createZepOnFinish` on the same call — that
persists every turn twice.

## Streaming (`streamText`)

The same pattern works unchanged for streaming. The middleware's
`transformParams` runs for `stream` calls too (injecting on each new user
turn), and both persistence paths fire once per turn for `streamText` just as
they do for `generateText`: `persist: true` accumulates `text-delta` parts and
persists on the stream's `finish` part, while `createZepOnFinish` fires from
`onFinish` after the whole tool loop completes.

```ts
import { streamText, wrapLanguageModel } from "ai";
import { openai } from "@ai-sdk/openai";
import { createZepMiddleware } from "@getzep/zep-vercel-ai";

const model = wrapLanguageModel({
  model: openai("gpt-5-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1", persist: true }),
});

const result = streamText({
  model,
  prompt: "I just adopted a beagle named Cooper.",
});

for await (const chunk of result.textStream) process.stdout.write(chunk);
```

Prefer to set `system:` yourself instead of using the middleware? Fetch the
block with `getZepContext` and persist with `persistZepTurn` (or
`createZepOnFinish`) directly. See [`examples/stream-text.ts`](./examples/stream-text.ts).

## The layers in detail

### `createZepMiddleware({ client, threadId, ... })`

Returns a Vercel AI SDK `LanguageModelMiddleware` (`specificationVersion: "v3"`)
for `wrapLanguageModel`.

- `transformParams` fetches the Context Block (`thread.getUserContext`, or a
  custom `contextBuilder`) and prepends it as a `system` message to the
  provider prompt — on both `generate` and `stream` calls — **only on a
  genuine new user turn** (detected by the last prompt message being a `user`
  message). On tool-loop continuation steps (last message is a `tool` result
  or an `assistant` tool call) it injects nothing, so the Context Block is
  fetched at most once per turn, not once per step. The injected text is
  `formatContext(context)` (default: renders the exported
  `DEFAULT_CONTEXT_TEMPLATE`, the canonical `<ZEP_CONTEXT>...</ZEP_CONTEXT>`
  wrapper shared by all Zep framework integrations, via literal `{context}`
  replacement). **Changed in 0.2.0:** the default wording is the canonical
  template, not the 0.1.x text — pass `formatContext` to restore the old
  output (see the CHANGELOG migration recipe).
- `persist` (default unset) opts into a **guaranteed persistence loop**: pass
  `true`, or `{ userName?, assistantName? }` to record speaker names. When
  set, the middleware also implements `wrapGenerate`/`wrapStream` — after the
  model's final step in a turn (`finishReason !== "tool-calls"`), it persists
  the user's message and the final assistant text via one fire-and-forget
  `thread.addMessages` call. When unset, `wrapGenerate`/`wrapStream` are
  `undefined` on the returned middleware (today's injection-only contract) —
  persist yourself with `createZepOnFinish`.
- `contextBuilder` replaces the default `thread.getUserContext` retrieval with
  a custom async function: `(input: ZepContextBuilderInput) => Promise<string | undefined>`,
  where `input` is `{ client, userId?, threadId, userMessage, params }`. Return
  `undefined` to inject nothing for that turn. Runs inside the same try/catch
  as the default retrieval — a rejection is logged and degrades to "no context
  injected", never crashing the call. The builder's result is still passed
  through `formatContext`.

Other options: `userId` (threaded to `contextBuilder`), `templateId` (custom
Zep Context Block layout; ignored when `contextBuilder` is set), and `logger`.
Implementation: [`src/middleware.ts`](./src/middleware.ts).

### `createZepOnFinish({ client, threadId, user?, userId?, ... })`

Returns an AI SDK `onFinish` callback that persists the whole turn **once** —
the user's input plus the final assistant text from the event — via
`thread.addMessages`. `onFinish` fires exactly once per turn (after the entire
tool loop completes) for both `generateText` and `streamText`, so this records
exactly one user message and one assistant message per turn and never writes
intermediate tool-call preamble. Supply the user side via `user` (a string, or a
`(event) => string` resolver); the assistant side is taken from `event.text`.

Use this **or** `createZepMiddleware({ ..., persist: true })` — not both. Both
paths write one `thread.addMessages` call per turn; enabling both persists
every turn twice.

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
| `zepSearch` | `graph.search` | Free-text search over the bound graph; returns relevant facts. See "Pin-or-expose search parameters" below. |
| `zepRemember` | `thread.addMessages` / `graph.add` | Persists a message (a `role` + bound thread; capped at Zep's 4,096-char message limit) or a general fact (`graph.add`; capped at Zep's 10,000-char limit). Over-long content is truncated with a lengths-only warning, never dropped. |
| `zepContext` | `thread.getUserContext` | Returns the whole-user-graph Context Block on demand. |

Each tool is also exported as a standalone factory (`createZepSearchTool`,
`createZepRememberTool`, `createZepContextTool`). Implementation:
[`src/tools.ts`](./src/tools.ts).

#### Pin-or-expose search parameters (`createZepSearchTool`)

By default, `createZepSearchTool`'s Zod input schema exposes every
`graph.search` knob to the model — `scope` (`edges`, `nodes`, `episodes`,
`observations`, `thread_summaries`, `auto`), `reranker` (`rrf`, `mmr`,
`node_distance`, `episode_mentions`, `cross_encoder`), `limit`, `mmrLambda`,
and `centerNodeUuid` — alongside the always-required `query`. Each parameter
is independently tri-state at construction time:

- **`pinnedParams: { scope: "edges" }`** — fixes the value; hidden from the
  model's schema; always sent.
- **`hiddenParams: ["mmrLambda", "centerNodeUuid"]`** — removed from the
  model's schema *without* pinning; simply omitted from the `graph.search`
  call, so Zep's own server-side default applies.
- **Omitted from both** — exposed to the model with the documented default
  (e.g. `scope` defaults to `"edges"`).

`searchFilters` and the new `bfsOriginNodeUuids` are always constructor-only —
never exposed to the model, always applied when set. The legacy `scope`,
`reranker`, and `limit` constructor arguments still work; they pin (and hide)
their parameter, equivalent to the corresponding `pinnedParams` entry.

```ts
// Model chooses scope/reranker/limit/mmrLambda/centerNodeUuid (new default).
const tool = createZepSearchTool({ client, binding: { userId: "u1" } });

// Restore the pre-0.2.0 "model only sees query" behavior.
const pinnedTool = createZepSearchTool({
  client,
  binding: { userId: "u1" },
  pinnedParams: { scope: "edges", limit: 10 },
  hiddenParams: ["reranker", "mmrLambda", "centerNodeUuid"],
});
```

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

## Provisioning: `ensureZepUserAndThread({ client, userId, threadId, ..., onUserCreated? })`

Idempotently creates the Zep user and thread before the first turn
(create-then-catch-conflict — an already-exists response is treated as
success). Pass `onUserCreated: async (client, userId) => { ... }` to run
one-time setup — per-user ontology, custom instructions, seeding a user
summary — **exactly once**, immediately after the user is genuinely created
(never on an already-exists path). Hook errors are logged, not thrown: the
function's `Promise<boolean>` keeps meaning "the user and thread are ready",
not "the hook succeeded".

```ts
await ensureZepUserAndThread({
  client,
  userId: "u1",
  threadId: "t1",
  firstName: "Jane",
  onUserCreated: async (zep, userId) => {
    // e.g. seed an initial graph fact or send a welcome event for this user.
    await zep.graph.add({ userId, type: "text", data: "New user onboarded." });
  },
});
```

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
