# Zep integration for Mastra

`@getzep/zep-mastra` adds Zep long-term memory to Mastra agents. It uses the
Zep v4 SDK and server-generated UUIDs.

## Install

```bash
npm install @getzep/zep-mastra @getzep/zep-cloud @mastra/core
```

The package requires Node.js 20 or later. `@mastra/core` is a peer dependency.
See [SETUP.md](./SETUP.md) for API key setup.

## Create a user and thread

Create Zep resources before the first agent turn. Save the returned UUIDs in
your application database and reuse them. The helper does not use a
client-selected ID and does not look up resources.

```ts
import { ZepClient } from "@getzep/zep-cloud";
import { createZepUserAndThread } from "@getzep/zep-mastra";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
const identity = await createZepUserAndThread({
  client,
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
});

if (!identity) throw new Error("Could not create the Zep user and thread.");
// Save identity.userUuid, identity.graphUuid, and identity.threadUuid.
```

`createZepUserAndThread` accepts `metadata`, `timeZone`, and
`disableDefaultOntology`. It also accepts a `logger`. Its `onUserCreated` hook
runs after user creation and before thread creation. Hook errors are logged and
do not stop thread creation. Zep errors are logged and return `null`.

## Automatic memory

Use processors when stored content is trusted and application-authored. The
input processor retrieves context before the model call. The output processor
persists the latest user message and the assistant's final response afterward.

```ts
import { Agent } from "@mastra/core/agent";
import { createZepProcessors } from "@getzep/zep-mastra";

const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  graphUuid: identity.graphUuid,
  threadUuid: identity.threadUuid,
});

const agent = new Agent({
  id: "memory-agent",
  name: "Memory Agent",
  instructions: "Use trusted memory to personalize responses.",
  model: "openai/gpt-5-mini",
  inputProcessors: [inputProcessor],
  outputProcessors: [outputProcessor],
});
```

The input processor uses `thread.getContext` by default. Configure
`templateUuid` or provide a `contextBuilder`, `contextTemplate`, or
`formatContext`. The `formatContext` option takes precedence over the template.
A custom builder receives the client, resolved UUIDs, latest user message, and
request context. It can use `graph.searchEdges` or another supported V4
operation. A builder that returns `undefined` adds no context.

The output processor persists the latest user message and the final assistant
text. If generation stops during a tool call, it still persists the user
message. Both processors accept a `logger`.

The processors accept a synchronous or asynchronous `resolveIdentity` callback
for per-call `graphUuid` and `threadUuid` values from Mastra's `requestContext`.
Values returned by the callback override bound UUIDs. Omitted values fall back
to the bound UUIDs. Missing identity, Zep errors, and context-formatting errors
do not stop the agent turn. The processors pass messages through and log a
warning. The input processor does not call `abort()`.

Context injection uses a system message. Do not use this path for untrusted
user or third-party content. Use retrieval tools for untrusted context.

## Tools

`createZepToolset` returns `zepRemember`, `zepSearch`, and `zepContext` tools.
You can also create each tool separately.
The toolset accepts `searchScope` and `searchLimit` to pin search settings.
Use `defaultMessageName` when a remembered message needs a name that the model
does not provide. The toolset and standalone factories accept `logger`.

- `zepRemember` stores a recognized conversational role with
  `thread.addMessages`. Other content is ingested with `graph.episode.add`.
- `zepRemember` maps common role aliases to Zep's role values. It omits an
  unknown role and ingests the content as an episode.
- `zepSearch` supports `edges`, `nodes`, `episodes`, `observations`,
  `thread_summaries`, and `auto`. The `auto` scope uses `graph.getContext`.
- `zepContext` retrieves a context block with `thread.getContext`. It accepts
  `templateUuid`.

The search tool exposes `scope`, `reranker`, `limit`, `mmrLambda`, and
`centerNodeUuid` by default. It defaults to `edges`, `rrf`, and a limit of 10.
Use `pinnedParams` to fix a value and hide it from the model. Use
`hiddenParams` to omit a value from the schema and request. `filters` and
`bfsOriginNodeUuids` are constructor-only. The legacy
`scope`/`reranker`/`limit` options also pin their values. Search limits are
clamped to 1–50.

For `auto`, the V4 `GraphContextRequest` accepts the query and supported
filters. It does not accept the scoped-search reranker, page-size, MMR,
center-node, or BFS-origin fields. The integration does not send those fields
to `graph.getContext`.

Tool errors are logged and returned as non-fatal tool results. The tools accept
the same `resolveIdentity` callback and a `logger`. The callback resolves
UUIDs from each tool call's `requestContext`.

## Graph bindings

Use the `graphUuid` from `createZepUserAndThread` for a user graph. For shared
knowledge, use the UUID returned by `graph.create`. A graph-only operation such
as `zepSearch` or `zepRemember` can use a binding with only `graphUuid`.
Context retrieval requires `threadUuid`.

## Upgrade from 0.2.x

Version 0.3.0 changes the package to the Zep v4 SDK. Bind with `graphUuid` and
`threadUuid`, and replace `ensureZepUserAndThread` with
`createZepUserAndThread`. The new helper creates resources and returns
`{ userUuid, graphUuid, threadUuid }`; it is not idempotent. Rename
`templateId` to `templateUuid` and `searchFilters` to `filters`.

## Development

```bash
npm install
npm run typecheck
npm run lint
npm test
npm run build
```

Set `ZEP_API_KEY` to run the live test:

```bash
ZEP_API_KEY="your-zep-api-key" npm test -- test/live.test.ts
```
