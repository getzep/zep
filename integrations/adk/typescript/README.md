# Zep Google ADK Integration (TypeScript)

Long-term memory for [Google ADK](https://github.com/google/adk) TypeScript agents, powered by [Zep](https://www.getzep.com). The integration persists conversation turns to Zep and injects a relevant, prompt-ready Context Block into every model call, so your agent remembers users across sessions.

Built on the official [`@google/adk`](https://www.npmjs.com/package/@google/adk) and [`@getzep/zep-cloud`](https://www.npmjs.com/package/@getzep/zep-cloud) SDKs (Zep V3).

## Installation

```bash
npm install @getzep/zep-adk @google/adk @getzep/zep-cloud
```

`@google/adk` is a peer dependency (`^1.2.0`) — install the version your app uses; this package is built and tested against `@google/adk@1.2.0` and verified stable across the `1.x` line.

## Choosing a component

zep-adk ships the same set of capabilities across Python, TypeScript, and Go, though the exact symbol names differ per language's ADK idioms:

| Capability | Python | TypeScript | Go |
|---|---|---|---|
| guaranteed context injection | `ZepContextTool` | `ZepContextTool` or `createZepBeforeModelCallback` | `NewBeforeModelCallback` |
| assistant-turn persistence | `create_after_model_callback` | `createZepAfterModelCallback` | `NewAfterModelCallback` |
| explicit provisioning + created signal | `ensure_user`/`ensure_thread` | `ensureUser`/`ensureThread` | `EnsureUser`/`EnsureThread` |
| custom context block | `context_builder` | `contextBuilder` | `WithContextBuilder` |
| injection template | `context_template` | `contextTemplate` | `WithContextTemplate` |
| model-callable graph search (pin-or-expose, 6 scopes) | `ZepGraphSearchTool` | `ZepGraphSearchTool` | `NewGraphSearchTool` |
| ADK-native memory service | `ZepMemoryService` | `ZepMemoryService` | `NewMemoryService` |

Note: Go intentionally has no tool-based injection (callbacks are the Go-ADK-idiomatic hook).

Note: Go has no `onCreated` hook -- use the `created` bool returned by `EnsureUser`/`EnsureThread` instead. Go's `EnsureUser` takes positional `firstName`, `lastName`, `email` strings (pass `""` to omit).

## Quick start

Provision the Zep user and thread once, out-of-band, before the first turn — then wire the callbacks:

```ts
import { LlmAgent } from "@google/adk";
import { ZepClient } from "@getzep/zep-cloud";
import { createZepCallbacks, ensureUser, ensureThread } from "@getzep/zep-adk";

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// Provision once, e.g. during account/session onboarding — NOT on every turn.
await ensureUser(zep, {
  userId: "user-123",
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
});
await ensureThread(zep, { threadId: "thread-abc", userId: "user-123" });

// createZepCallbacks builds the before/after pair sharing one dedup guard —
// the recommended way to wire both callbacks together.
const { beforeModelCallback, afterModelCallback } = createZepCallbacks(zep, {
  userId: "user-123",
  threadId: "thread-abc",
  firstName: "Jane",
  lastName: "Smith",
});

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-2.5-flash",
  instruction: "You are a helpful assistant with long-term memory.",
  beforeModelCallback,
  afterModelCallback,
});
```

See [`examples/basic-agent.ts`](examples/basic-agent.ts) for a complete, runnable conversation, and [SETUP.md](SETUP.md) for signup, API keys, and running the example.

## How it works

The Zep loop is: create user → create thread → add messages → retrieve context. This package splits that loop in two: explicit, out-of-band provisioning (once, before the first turn) and the ADK request lifecycle (every turn).

### `ensureUser` / `ensureThread` (explicit provisioning)

Idempotent, out-of-band helpers. Call these once — during onboarding, account creation, or before the first turn of a new conversation — **before** the agent runs. They are create-then-catch-conflict: each calls the Zep SDK's create method directly and resolves to `true` if the resource was newly created, `false` if it already existed. Genuine failures (auth, network, 5xx) throw, so misconfiguration is caught immediately rather than silently swallowed.

`ensureUser` accepts an optional `onCreated` hook that runs exactly once, only for genuinely new users — the place to configure per-user ontology, custom instructions, or a user summary instruction. If the hook throws, the exception propagates (the user was still created; write `onCreated` to be idempotent so a retry is safe).

**The ADK turn path (`createZepBeforeModelCallback`, `createZepAfterModelCallback`, `ZepContextTool`) never creates users or threads itself** — it assumes they already exist. If a persist call targets a user/thread that hasn't been provisioned, it logs a warning naming `ensureUser()`/`ensureThread()` and the turn continues without Zep memory.

### `createZepBeforeModelCallback` (primary hook)

Returns a function for `LlmAgent.beforeModelCallback`. On each model call it:

1. Extracts the latest user message from the ADK context.
2. Resolves the Zep identity (see [Identity resolution](#identity-resolution)).
3. Persists the user message and retrieves a context block — either via a single `thread.addMessages({ returnContext: true })` round-trip (default), or by running a custom `contextBuilder` in parallel with message persistence (see [Custom context builders](#custom-context-builders-contextbuilder)).
4. Injects the context block into `request.config.systemInstruction`, rendered through `contextTemplate` (see [Injection template](#injection-template-contexttemplate)).

It always returns `undefined`, so the (mutated) request proceeds to the model.

The injection mutates `config.systemInstruction` directly. ADK's `appendInstructions` helper lives in a module that is not reachable under `NodeNext` resolution (the package only exports `"."`), so direct mutation is the supported path — an existing instruction is preserved and the Zep block is appended.

### Custom context builders (`contextBuilder`)

By default, the turn path retrieves context via `thread.addMessages({ returnContext: true })` — a single round-trip scoped to the thread's default context retrieval. For advanced use cases (multi-graph search, custom filtering, a different Context Block shape) pass a `contextBuilder` to `createZepBeforeModelCallback`, `ZepContextTool`, or `createZepCallbacks`:

```ts
import type { ContextBuilderInput } from "@getzep/zep-adk";

async function multiGraphBuilder(input: ContextBuilderInput): Promise<string | undefined> {
  const [userGraph, orgGraph] = await Promise.all([
    input.zep.graph.search({ userId: input.userId, query: input.userMessage, scope: "edges" }),
    input.zep.graph.search({ graphId: "org-kb", query: input.userMessage, scope: "edges" }),
  ]);
  const facts = [...(userGraph.edges ?? []), ...(orgGraph.edges ?? [])].map((e) => e.fact);
  return facts.length > 0 ? facts.join("\n") : undefined;
}

const beforeModelCallback = createZepBeforeModelCallback(zep, {
  userId: "user-123",
  threadId: "thread-abc",
  contextBuilder: multiGraphBuilder,
});
```

When `contextBuilder` is set, message persistence (`thread.addMessages`, without `returnContext`) and the builder run **concurrently** for lower latency. Each is isolated from the other's failure:

- If the builder rejects, a warning is logged and injection is skipped for that turn — but persistence still completes and the turn is marked as persisted (dedup) on success.
- If persistence rejects, a warning is logged and the turn is **not** marked as persisted (so it is retried on the next invocation) — but a successful builder result may still be injected into the prompt.

Return `undefined` (or an empty string) from the builder to skip injection for that turn without affecting persistence.

### Injection template (`contextTemplate`)

The retrieved context block is wrapped in a template before being injected into the system instruction. The default is:

```ts
export const DEFAULT_CONTEXT_TEMPLATE =
  "The following context is retrieved from Zep, the agent's long-term memory. " +
  "It contains relevant facts, entities, and prior knowledge about the user. " +
  "Use it to inform your responses.\n\n" +
  "<ZEP_CONTEXT>\n" +
  "{context}\n" +
  "</ZEP_CONTEXT>";
```

Pass a custom `contextTemplate` to `createZepBeforeModelCallback`, `ZepContextTool`, or `createZepCallbacks` to override it. The template must contain a literal `{context}` placeholder — **all** occurrences are replaced with the retrieved context text via plain string replacement (never a regex or format-string engine), so context text or a template containing `{`, `}`, `%`, or `$` is always safe to inject:

```ts
const beforeModelCallback = createZepBeforeModelCallback(zep, {
  contextTemplate: "Relevant memory:\n{context}",
});
```

This default template text is canonical across zep-adk's Python, Go, and TypeScript implementations.

### `createZepAfterModelCallback`

Returns a function for `LlmAgent.afterModelCallback` that persists the assistant's response to the Zep thread, so both sides of the conversation reach the user's graph. Intermediate tool-call turns and partial streaming chunks are skipped.

### `ZepContextTool` (tool-centric alternative)

A `BaseTool` subclass that performs the same persist-and-inject work via `processLlmRequest` — the hook ADK's own `PreloadMemoryTool` uses. It is **not** model-callable (`_getDeclaration()` returns `undefined`, `runAsync` is a no-op); it only preprocesses the outgoing request. Use it when you prefer composing memory through `LlmAgent.tools` instead of `beforeModelCallback`.

Pick **either** `createZepBeforeModelCallback` **or** `ZepContextTool` — running both persists each user message twice.

### `ZepGraphSearchTool`

A model-callable `BaseTool` that searches a Zep knowledge graph on demand. Set `graphId` to search a standalone graph, or omit it to search the current user's graph.

Every search parameter is **tri-state** at construction time:

| State | How to set it | Effect |
|-------|----------------|--------|
| **Pinned** | a concrete value, e.g. `scope: "edges"` | Hidden from the model's tool schema. Always used, even if the model sends a different value for that argument. |
| **Hidden** | `null` | Hidden from the model's tool schema AND omitted from the `graph.search` call entirely. |
| **Exposed** (default) | omit the option (`undefined`) | Included in the model's tool schema with the default below, so the model chooses a value per call. |

| Option | Model param | Default when exposed | Notes |
|--------|-------------|------------------------|-------|
| `scope` | `scope` | `"edges"` | `"edges"` \| `"nodes"` \| `"episodes"` \| `"observations"` \| `"thread_summaries"` \| `"auto"` |
| `reranker` | `reranker` | `"rrf"` | `"rrf"` \| `"mmr"` \| `"node_distance"` \| `"episode_mentions"` \| `"cross_encoder"` |
| `limit` | `limit` | `10` | Maximum number of results |
| `mmrLambda` | `mmrLambda` | — (no default; omitted unless set) | Diversity (`0.0`) vs. relevance (`1.0`); only used when `reranker` is `"mmr"` |
| `centerNodeUuid` | `centerNodeUuid` | — (no default; omitted unless set) | Required when `reranker` is `"node_distance"` |

`searchFilters` and `bfsOriginNodeUuids` are always constructor-only — never exposed to the model, always applied to every search when set.

An invalid enum value sent by the model (e.g. an unsupported `scope`) falls back to the documented default and logs a warning; it never throws.

```ts
// Fully dynamic: the model chooses scope, reranker, limit, mmrLambda, centerNodeUuid.
new ZepGraphSearchTool({ zep, userId: "user-123" });

// Pin scope + limit, but let the model choose the reranker.
new ZepGraphSearchTool({ zep, userId: "user-123", scope: "edges", limit: 5 });

// Restore the pre-0.2.0 behavior: model only ever sees `query`.
new ZepGraphSearchTool({
  zep,
  userId: "user-123",
  scope: "edges",
  reranker: "rrf",
  limit: 10,
  mmrLambda: null,
  centerNodeUuid: null,
});
```

### `ZepMemoryService` (ADK-native memory service)

`ZepMemoryService` implements ADK's `BaseMemoryService` interface directly, so it plugs into ADK's own memory extension point rather than a callback or tool. Register it on a `Runner` and the model can call ADK's built-in `loadMemory` / `preloadMemory` tools to search Zep whenever *it* decides memory is relevant:

```ts
import { Runner, LlmAgent, InMemorySessionService, LOAD_MEMORY } from "@google/adk";
import { ZepClient } from "@getzep/zep-cloud";
import { ZepMemoryService } from "@getzep/zep-adk";

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-2.5-flash",
  instruction: "You are a helpful assistant. Use load_memory to recall relevant facts about the user.",
  tools: [LOAD_MEMORY],
});

const runner = new Runner({
  agent,
  appName: "my_app",
  sessionService: new InMemorySessionService(),
  memoryService: new ZepMemoryService({ zep, scope: "edges" }),
});
```

**This requires the full `Runner`, not `InMemoryRunner`.** `InMemoryRunner`'s constructor does not accept a `memoryService` option — only `Runner`'s `RunnerConfig` does. `InMemoryRunner` also supplies its own in-memory session service; wiring `Runner` directly means providing `sessionService` yourself (an `InMemorySessionService` is fine for development).

**When to use `ZepMemoryService` vs. the callbacks/tools above:**

| | Trigger | Use when |
|---|---|---|
| `ZepMemoryService` | Model-opt-in — the model decides per turn whether to call `loadMemory`/`preloadMemory` | You want the model to actively decide when memory is relevant, or you're wiring into ADK code paths that already expect a `memoryService` (ADK's own memory tools, evaluation harnesses) |
| `ZepContextTool` / `createZepBeforeModelCallback` | Guaranteed — runs on every turn automatically | You want Zep context always present, regardless of whether the model would have thought to ask for it |

The two extension points are complementary: pair `ZepContextTool` (always-on context) with `ZepMemoryService` (explicit on-demand search) so the model can dig further when the always-on context isn't enough.

`ZepMemoryService.addSessionToMemory` is an intentional no-op — see [Why `addSessionToMemory` is a no-op](#why-addsessiontomemory-is-a-no-op) below.

#### Why `addSessionToMemory` is a no-op

ADK calls `addSessionToMemory` to flush a session's conversation into a memory store, typically at session end. Zep already ingests every message live, turn-by-turn, via `ZepContextTool` or `createZepBeforeModelCallback`/`createZepAfterModelCallback` — so re-ingesting the whole session again in `addSessionToMemory` would duplicate that conversation in the graph a second time. `ZepMemoryService.addSessionToMemory` therefore just logs a debug message and returns, matching the same intentional no-op in the Go (`NewMemoryService`) and Python (`ZepMemoryService`) integrations.

`ZepMemoryService` accepts:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `zep` | `ZepClient` | required | An initialised client. The caller owns its lifecycle. |
| `scope` | `Zep.GraphSearchScope` | `"edges"` | Search scope for every `searchMemory` call: `"edges"`, `"nodes"`, `"episodes"`, `"observations"`, `"thread_summaries"`, or `"auto"` (Zep's pre-assembled Context Block, returned as a single memory entry). |
| `limit` | `number` | SDK default | Maximum results per search. |
| `logger` | `Logger` | `console`-backed | Logger for Zep failures and unsupported-scope warnings. |

`searchMemory` never throws: an unsupported scope is rejected before any network call (logged as a warning, empty response returned), and a Zep failure is caught, logged (lengths only, never query or result content), and also returns an empty response — so a memory lookup can never break the agent.

## Identity resolution

Both callbacks and tools resolve a Zep `userId` and `threadId` per turn, in this order:

1. Explicit options passed at construction (`userId` / `threadId`).
2. Session-state keys (`zep_user_id` / `zep_thread_id`).
3. The ADK session's `userId` / `sessionId`.

Omitting the IDs lets one callback or tool serve every user in a shared-agent deployment — set the per-user identity in ADK session state instead.

### Session-state keys

| Key | Purpose | Default |
|-----|---------|---------|
| `zep_user_id` | Override the Zep user ID | ADK `userId` |
| `zep_thread_id` | Override the Zep thread ID | ADK `sessionId` |
| `zep_first_name` | First name (anchors the user's graph node) | — |
| `zep_last_name` | Last name | — |

Always provide a name so persisted messages are attributed to the user in the graph. The user's email lives on the Zep user profile — pass it to `ensureUser` during provisioning, not session state.

## API

| Export | Kind | Description |
|--------|------|-------------|
| `ensureUser(zep, options)` | function | Explicit, idempotent, out-of-band user provisioning. Returns `true` if newly created. |
| `ensureThread(zep, options)` | function | Explicit, idempotent, out-of-band thread provisioning. Returns `true` if newly created. |
| `createZepBeforeModelCallback(zep, options?)` | factory | Primary hook: persist user turn + inject Context Block. |
| `createZepAfterModelCallback(zep, options?)` | factory | Persist the assistant response. |
| `createZepCallbacks(zep, options?)` | factory | Recommended: builds the before/after callback pair sharing one `TurnDedup` guard. |
| `ZepContextTool` | `BaseTool` | Tool-centric alternative to the before-model callback. |
| `ZepGraphSearchTool` | `BaseTool` | Model-callable graph search. |
| `ZepMemoryService` | `BaseMemoryService` | ADK-native memory service for `Runner`'s `memoryService` option. |
| `resolveIdentity`, `extractText`, `STATE_KEYS` | helpers | Identity / content utilities. |
| `formatContextInstruction`, `persistAndInject` | helpers | Building blocks for custom wiring. |
| `DEFAULT_CONTEXT_TEMPLATE` | constant | The default injection template (see [Injection template](#injection-template-contexttemplate)). |
| `ContextBuilder`, `ContextBuilderInput` | types | Custom context-builder function type and its input (see [Custom context builders](#custom-context-builders-contextbuilder)). |
| `TurnDedup` | class | Same-turn dedup guard used by `createZepBeforeModelCallback`'s `dedup` option; share one instance across multiple callback calls if you construct them independently instead of via `createZepCallbacks`. |
| `ZepIdentityError` | error | Thrown only when identity cannot be resolved. |

Constructor options for the callbacks and tools share `userId`, `threadId`, `firstName`, `lastName`, `ignoreRoles`, `contextBuilder`, `contextTemplate`, and `logger`. See the inline TSDoc for full signatures.

### `ensureUser` options

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `userId` | `string` | Yes | The Zep user ID to create. |
| `firstName` | `string` | No | Passed through to `zep.user.add`. |
| `lastName` | `string` | No | Passed through to `zep.user.add`. |
| `email` | `string` | No | Passed through to `zep.user.add`. |
| `onCreated` | `(zep, userId) => Promise<void>` | No | Runs exactly once, only when the user is newly created; awaited before `ensureUser` returns; errors propagate. |

Returns `Promise<boolean>` — `true` if newly created, `false` if it already existed. Throws on genuine failures (auth, network, 5xx).

### `ensureThread` options

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `threadId` | `string` | Yes | The Zep thread ID to create. |
| `userId` | `string` | Yes | The Zep user ID that owns the thread (must already exist). |

Returns `Promise<boolean>` — `true` if newly created, `false` if it already existed. Throws on genuine failures (auth, network, 5xx).

## Error handling

Two different error philosophies apply, by design:

- **Provisioning (`ensureUser` / `ensureThread`)** is meant to fail loudly. Genuine failures (auth, network, 5xx) throw, so misconfiguration is caught before the agent ever runs rather than silently swallowed. Only an "already exists" conflict is treated as success (returns `false`).
- **The turn path** (the callbacks and tools) never throws a Zep error. Every Zep API call there is wrapped: failures are logged through the configured `logger` (defaults to `console`) and swallowed. A Zep outage degrades the agent to no-memory behaviour — it never crashes the host agent. The only thrown error on the turn path is `ZepIdentityError`, and only from the standalone `resolveIdentity` helper; inside the callbacks and tools it is caught and the turn proceeds. If a persist call targets a user/thread that was never provisioned, a warning naming `ensureUser()`/`ensureThread()` is logged instead.

Zep ingestion is asynchronous — a just-added message is not instantly retrievable. Design for eventual availability (the example waits before testing recall).

## Migrating from 0.1.x

0.2.0 changes `ZepGraphSearchTool` from "always pinned" to a pin-or-expose model:

- **Before:** `scope`, `reranker`, and `limit` were always pinned at construction (defaulting to `"edges"` / `"rrf"` / `10`); the model's tool schema only ever contained `query`.
- **After:** any option you *omit* is now **exposed to the model** with the same default, so the model can choose `scope` / `reranker` / `limit` / `mmrLambda` / `centerNodeUuid` per call. This is a **breaking change** in tool-schema shape and in what the model is allowed to control.
- **To restore the old, fully-pinned behavior**, pass concrete values for `scope`, `reranker`, and `limit`, and pin the two previously-absent numeric/string params to `null` so they don't appear in the schema either:

  ```ts
  // Before (0.1.x): implicit — the model never saw more than `query`.
  new ZepGraphSearchTool({ zep, userId: "user-123" });

  // After (0.2.0+): pin everything explicitly to reproduce the old behavior.
  new ZepGraphSearchTool({
    zep,
    userId: "user-123",
    scope: "edges",
    reranker: "rrf",
    limit: 10,
    mmrLambda: null,
    centerNodeUuid: null,
  });
  ```

- **New constructor-only options** `searchFilters` and `bfsOriginNodeUuids` are additive — they were not previously supported and default to unset (omitted from every `graph.search` call).

0.2.0 also introduces a configurable `contextTemplate` and changes the default injection wording to `DEFAULT_CONTEXT_TEMPLATE`, the string canonical across Python, Go, and TypeScript:

- **The default header wording changed.** 0.1.x hardcoded `"...Use it to inform your response."` (singular). 0.2.0's `DEFAULT_CONTEXT_TEMPLATE` ends `"...Use it to inform your responses."` (plural) and is now overridable via `contextTemplate`. The `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper is unchanged. If you depend on the exact previous wording, pass it explicitly as `contextTemplate`.
- **`formatContextInstruction` gained a second, optional `template` parameter** (`formatContextInstruction(contextBlock, template?)`); existing single-argument call sites are unaffected.

0.2.0 also removes lazy, in-band Zep user/thread creation from the ADK turn path in favor of explicit, out-of-band provisioning:

- **Lazy creation is gone.** `createZepBeforeModelCallback`, `createZepAfterModelCallback`, and `ZepContextTool` no longer call `zep.user.add` / `zep.thread.create`. If the user/thread don't exist yet, persistence for that turn fails with a logged warning (the turn continues without Zep memory) instead of silently creating them.
- **Call `ensureUser` / `ensureThread` yourself**, once, before the first turn — typically in your app's account or session onboarding code:

  ```ts
  import { ensureUser, ensureThread } from "@getzep/zep-adk";

  await ensureUser(zep, { userId, firstName, lastName, email });
  await ensureThread(zep, { threadId, userId });
  ```

- **`ZepResourceManager` is gone.** The class that used to own lazy creation and same-turn dedup has been replaced by two independent pieces: the `ensureUser` / `ensureThread` functions above (provisioning) and the exported `TurnDedup` guard (used only by `createZepBeforeModelCallback` — the after-model callback has no dedup state of its own). If you passed a shared `resources: ZepResourceManager` instance to keep the before/after callbacks in sync, pass a `TurnDedup` instance as `dedup` instead — or just use `createZepCallbacks`, which creates and wires one automatically:

  ```ts
  // Before (0.1.x)
  import { ZepResourceManager, createZepBeforeModelCallback, createZepAfterModelCallback } from "@getzep/zep-adk";
  const resources = new ZepResourceManager(zep, logger);
  createZepBeforeModelCallback(zep, { resources, ...identity });
  createZepAfterModelCallback(zep, { resources, ...identity });

  // After (0.2.0+)
  import { createZepCallbacks } from "@getzep/zep-adk";
  const { beforeModelCallback, afterModelCallback } = createZepCallbacks(zep, identity);
  ```

- **Any prior per-user setup pattern is replaced by `onCreated`.** If you previously ran one-time setup for new users right after your own `user.add` call, move it into `ensureUser`'s `onCreated` hook — it fires exactly once, only for genuinely new users, and its errors propagate (create it to be idempotent so a retry is safe):

  ```ts
  await ensureUser(zep, {
    userId,
    firstName,
    lastName,
    onCreated: async (zep, userId) => {
      // one-time setup: ontology, custom instructions, summary instructions, ...
    },
  });
  ```

## Development

```bash
npm install
npm run typecheck   # tsc --noEmit (NodeNext + strict)
npm test            # vitest (mocked @getzep/zep-cloud)
npm run build       # tsup → dist (ESM + .d.ts)
npm run example     # run examples/basic-agent.ts
```

## Requirements

- Node.js >= 20
- `@google/adk` ^1.2.0 (peer)
- `@getzep/zep-cloud` >= 3.23.0

## License

Apache-2.0 — see [LICENSE](LICENSE).
