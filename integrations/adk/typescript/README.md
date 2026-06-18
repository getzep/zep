# Zep Google ADK Integration (TypeScript)

Long-term memory for [Google ADK](https://github.com/google/adk) TypeScript agents, powered by [Zep](https://www.getzep.com). The integration persists conversation turns to Zep and injects a relevant, prompt-ready Context Block into every model call, so your agent remembers users across sessions.

Built on the official [`@google/adk`](https://www.npmjs.com/package/@google/adk) and [`@getzep/zep-cloud`](https://www.npmjs.com/package/@getzep/zep-cloud) SDKs (Zep V3).

## Installation

```bash
npm install @getzep/zep-adk @google/adk @getzep/zep-cloud
```

`@google/adk` is a peer dependency — install the exact version your app uses (this package is built against `@google/adk@1.2.0`).

## Quick start

```ts
import { LlmAgent } from "@google/adk";
import { ZepClient } from "@getzep/zep-cloud";
import {
  createZepBeforeModelCallback,
  createZepAfterModelCallback,
} from "@getzep/zep-adk";

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-2.5-flash",
  instruction: "You are a helpful assistant with long-term memory.",
  beforeModelCallback: createZepBeforeModelCallback(zep, {
    userId: "user-123",
    threadId: "thread-abc",
    firstName: "Jane",
    lastName: "Smith",
  }),
  afterModelCallback: createZepAfterModelCallback(zep, {
    userId: "user-123",
    threadId: "thread-abc",
  }),
});
```

See [`examples/basic-agent.ts`](examples/basic-agent.ts) for a complete, runnable conversation, and [SETUP.md](SETUP.md) for signup, API keys, and running the example.

## How it works

The Zep loop is: create user → create thread → add messages → retrieve context. This package wires that loop into ADK's request lifecycle.

### `createZepBeforeModelCallback` (primary hook)

Returns a function for `LlmAgent.beforeModelCallback`. On each model call it:

1. Extracts the latest user message from the ADK context.
2. Resolves the Zep identity (see [Identity resolution](#identity-resolution)).
3. Lazily creates the Zep user and thread on first use.
4. Persists the user message and retrieves the Context Block in a single `thread.addMessages({ returnContext: true })` round-trip.
5. Injects the Context Block into `request.config.systemInstruction`.

It always returns `undefined`, so the (mutated) request proceeds to the model.

The injection mutates `config.systemInstruction` directly. ADK's `appendInstructions` helper lives in a module that is not reachable under `NodeNext` resolution (the package only exports `"."`), so direct mutation is the supported path — an existing instruction is preserved and the Zep block is appended.

### `createZepAfterModelCallback`

Returns a function for `LlmAgent.afterModelCallback` that persists the assistant's response to the Zep thread, so both sides of the conversation reach the user's graph. Intermediate tool-call turns and partial streaming chunks are skipped.

### `ZepContextTool` (tool-centric alternative)

A `BaseTool` subclass that performs the same persist-and-inject work via `processLlmRequest` — the hook ADK's own `PreloadMemoryTool` uses. It is **not** model-callable (`_getDeclaration()` returns `undefined`, `runAsync` is a no-op); it only preprocesses the outgoing request. Use it when you prefer composing memory through `LlmAgent.tools` instead of `beforeModelCallback`.

Pick **either** `createZepBeforeModelCallback` **or** `ZepContextTool` — running both persists each user message twice.

### `ZepGraphSearchTool`

A model-callable `BaseTool` that searches a Zep knowledge graph on demand. The model supplies a `query`; `scope`, `reranker`, and `limit` are pinned at construction. Set `graphId` to search a standalone graph, or omit it to search the current user's graph.

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
| `zep_email` | Email address | — |

Always provide a name (ideally last name + email) so Zep resolves the user's identity in the graph.

## API

| Export | Kind | Description |
|--------|------|-------------|
| `createZepBeforeModelCallback(zep, options?)` | factory | Primary hook: persist user turn + inject Context Block. |
| `createZepAfterModelCallback(zep, options?)` | factory | Persist the assistant response. |
| `ZepContextTool` | `BaseTool` | Tool-centric alternative to the before-model callback. |
| `ZepGraphSearchTool` | `BaseTool` | Model-callable graph search. |
| `resolveIdentity`, `extractText`, `STATE_KEYS` | helpers | Identity / content utilities. |
| `formatContextInstruction`, `persistAndInject` | helpers | Building blocks for custom wiring. |
| `ZepResourceManager` | class | Lazy user/thread creation with an "already exists" guard. |
| `ZepIdentityError` | error | Thrown only when identity cannot be resolved. |

Constructor options for the callbacks and tools share `userId`, `threadId`, `firstName`, `lastName`, `email`, `ignoreRoles`, and `logger`. See the inline TSDoc for full signatures.

## Error handling

Every Zep API call is wrapped: failures are logged through the configured `logger` (defaults to `console`) and swallowed. A Zep outage degrades the agent to no-memory behaviour — it never crashes the host agent. The only thrown error is `ZepIdentityError`, and only from the standalone `resolveIdentity` helper; inside the callbacks and tools it is caught and the turn proceeds.

Zep ingestion is asynchronous — a just-added message is not instantly retrievable. Design for eventual availability (the example waits before testing recall).

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
- `@google/adk` 1.2.0 (peer)
- `@getzep/zep-cloud` >= 3.23.0

## License

Apache-2.0 — see [LICENSE](LICENSE).
