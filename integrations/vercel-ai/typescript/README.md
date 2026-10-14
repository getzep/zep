# Zep Vercel AI SDK Integration

`@getzep/zep-vercel-ai` adds Zep V4 memory to the Vercel AI SDK v6. It provides
server-side identity creation, turn middleware, persistence helpers, and model
tools.

> **Security:** Do not put untrusted user or retrieved graph content in
> privileged system or developer instructions. Use the `zepContext` or
> `zepSearch` tools to return untrusted context as tool results. Use middleware
> system-message injection only for trusted, application-authored context.

## Installation

```bash
npm install @getzep/zep-vercel-ai @getzep/zep-cloud ai zod
```

See [SETUP.md](./SETUP.md) for account setup, API keys, and example commands.

## Quick start

Create a user and thread before the first model call. Zep returns the user,
graph, and thread UUIDs. Bind the graph and thread UUIDs to the tools:

```ts
import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { generateText, stepCountIs } from "ai";
import { createZepTools, createZepUserAndThread } from "@getzep/zep-vercel-ai";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
const identity = await createZepUserAndThread({
  client,
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
});
if (!identity) throw new Error("Could not create Zep user and thread.");

const tools = createZepTools(client, {
  binding: { graphUuid: identity.graphUuid, threadUuid: identity.threadUuid },
});
const result = await generateText({
  model: openai.chat("gpt-5-mini"),
  prompt: "What do you remember about me?",
  tools,
  stopWhen: stepCountIs(5),
});
```

The complete examples are in [`examples/`](./examples).

## Middleware and persistence

`createZepMiddleware({ client, threadUuid, ... })` returns an AI SDK
`LanguageModelMiddleware`. It retrieves context with `thread.getContext` and
injects the formatted block as a leading system message on a new user turn.
It does not inject context on tool-loop continuation steps.

- `formatContext` formats the retrieved context. The default uses
  `DEFAULT_CONTEXT_TEMPLATE`.
- `templateUuid` selects a Zep context template.
- `contextBuilder` replaces default retrieval. It receives
  `{ client, userUuid?, threadUuid, userMessage, params }` and can return
  `undefined` to skip injection.
- Zep or builder errors are logged. They do not fail the model call.
- Middleware persistence is optional. Set `persist: true` or pass
  `{ userName?, assistantName? }` to persist each completed turn once.

You can instead use `createZepOnFinish` on `generateText` or `streamText`.
It persists the user input and final assistant text once after the tool loop.
Do not set middleware `persist` and `createZepOnFinish` for the same call.
Either persistence path logs Zep errors and does not fail the host call.

```ts
const { text } = await generateText({
  model,
  prompt,
  onFinish: createZepOnFinish({
    client,
    threadUuid: identity.threadUuid,
    user: prompt,
  }),
});
```

Middleware also requires `threadUuid`. Its `userUuid` option is passed to the
context builder; it does not perform a lookup.

## Helpers

- `getZepContext(client, threadUuid, options?)` returns a trimmed context string
  from `thread.getContext`. It accepts `templateUuid` and an optional logger.
  It returns an empty string if retrieval fails.
- `persistZepTurn(client, threadUuid, turn, options?)` writes available user
  and assistant messages in one `thread.addMessages` call. It truncates
  messages to the supported limit. Set `returnContext` to request context with
  the write.
- `createZepOnFinish(options)` builds the AI SDK finish callback. It accepts a
  string or resolver for the user message and optional speaker names.
- `createZepUserAndThread(options)` creates a user, calls `onUserCreated` after
  user creation, then creates a thread. It returns
  `{ userUuid, graphUuid, threadUuid }`, or `null` if provisioning fails.
  Hook errors are logged and do not change the returned identity.

Pass `onUserCreated: async (client, userUuid) => { ... }` for one-time setup
after Zep creates a user. The hook receives the server-generated user UUID.
Hook errors do not change the returned identity.

These helpers do not accept caller-selected Zep resource UUIDs and do not
perform runtime lookup calls.

## Tools

`createZepTools(client, { binding, ... })` returns `zepSearch`, `zepRemember`,
and `zepContext`. Each tool handles Zep errors and returns a safe result instead
of throwing.

| Tool | Operation | Behavior |
|---|---|---|
| `zepSearch` | V4 graph search | Searches the bound graph and returns formatted facts. |
| `zepRemember` | `thread.addMessages` or `graph.episode.add` | Saves a conversational message or a text episode. |
| `zepContext` | `thread.getContext` | Retrieves a context block for the bound thread. |

You can also create each tool separately with `createZepSearchTool`,
`createZepRememberTool`, or `createZepContextTool`.

### Search scopes and parameters

The search tool supports all six scopes:

- `edges` searches facts and relationships.
- `nodes` searches entities and summaries.
- `episodes` searches source text.
- `observations` searches derived memories.
- `thread_summaries` searches thread summaries.
- `auto` uses `graph.getContext`.

By default, the model can choose `scope`, `reranker`, `limit`, `mmrLambda`, and
`centerNodeUuid`. Use `pinnedParams` to fix a value and hide it from the model.
Use `hiddenParams` to hide a parameter without sending it, so the server uses
its default. The older `scope`, `reranker`, and `limit` constructor options
remain as aliases for pinned values. Limits are clamped to the supported
`[1, 50]` range.

`filters` and `bfsOriginNodeUuids` are constructor-only. They are not exposed
to the model. The scoped search methods receive the supported V4 parameters,
including `pageSize`, filters, reranker, MMR, center-node UUID, and BFS origins.
The V4 `GraphContextRequest` does not accept those scoped-search controls.
Therefore, `auto` sends its query and supported filters only.

### Graph bindings

Bind `graphUuid` for search and episode writes. Bind `threadUuid` for context
retrieval and conversational writes. A user graph can use both UUIDs. A shared
graph can use `graphUuid` without a thread. `zepContext` requires a thread UUID.
Tools return a safe empty or not-configured result when a required UUID is
missing.

`zepRemember` writes a message with `thread.addMessages` when a role and thread
are supplied. Other data is written with `graph.episode.add`. It truncates
long content to conservative limits of 4,000 characters for messages and 9,900
characters for episodes. Zep accepts messages up to 4,096 characters and
episodes up to 10,000 characters. V4 does not define the `norole` role, so
unknown roles are omitted.

## Asynchronous ingestion

Zep ingests episodes asynchronously. A fact may not be available to search
immediately after a write. Design for eventual availability; do not depend on
read-after-write in the same turn.

## Development

```bash
npm install
npm run typecheck
npm run lint
npm test
npm run build
npm run example
```

The live test runs when `ZEP_API_KEY` is set. See `SETUP.md` for the required
OpenAI key.

## Requirements

- Node.js >= 20
- Vercel AI SDK `ai` >= 6 and `zod` 3 or 4 as peer dependencies
- `@getzep/zep-cloud` >= 4.0.0

## Links

- [Zep documentation](https://help.getzep.com)
- [Vercel AI SDK documentation](https://ai-sdk.dev/docs)
- [GitHub issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](./LICENSE).
