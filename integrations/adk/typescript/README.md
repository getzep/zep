# Zep integration for Google ADK (TypeScript)

This package connects a Google ADK agent to Zep v4. It persists conversation
messages, retrieves context, and supports model-initiated graph search.

## Install

```bash
npm install @getzep/zep-adk @getzep/zep-cloud @google/adk
```

Set `ZEP_API_KEY` in the application environment. See [SETUP.md](SETUP.md) for
the account and API key steps.

## Create a user and thread

Zep creates UUIDs for users and threads. Create each resource before the agent
runs, then store the returned UUIDs in the application database.

```ts
import { ZepClient } from "@getzep/zep-cloud";
import { createUser, createThread } from "@getzep/zep-adk";

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
const { userUuid, graphUuid } = await createUser(zep, {
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
  onCreated: async (client, newUserUuid) => {
    await configureUserGraph(client, newUserUuid);
  },
});
const { threadUuid } = await createThread(zep, { userUuid });
```

`createUser` returns `{ userUuid, graphUuid }`. `createThread` returns
`{ threadUuid, graphUuid }`. Each call creates a resource. The helpers do not
accept caller-selected UUIDs. Provisioning errors reject the promise.

The `onCreated` hook runs after each new user is created. Use it for per-user
setup, such as a custom ontology or extraction instruction.

## Persist messages and inject context

The before-model callback and `ZepContextTool` insert retrieved content into
the model's system instruction. Use them only when all graph content is trusted
and application-authored. Use `ZepGraphSearchTool` or `ZepMemoryService` for
user-provided or third-party content.

Use the paired callbacks to persist the user and assistant messages. The
before-model callback also injects the Zep Context Block into the system
instruction.

```ts
import { LlmAgent } from "@google/adk";
import { createZepCallbacks } from "@getzep/zep-adk";

const { beforeModelCallback, afterModelCallback } = createZepCallbacks(zep, {
  userUuid,
  threadUuid,
  contextTemplate: "Relevant memory:\n{context}",
});

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-3.7-flash",
  instruction: "Use the supplied context to answer the user.",
  beforeModelCallback,
  afterModelCallback,
});
```

The callbacks and `ZepContextTool` accept `ignoreRoles`, `contextBuilder`, and
`contextTemplate`. A context builder receives `zep`, `userUuid`, `threadUuid`,
`userMessage`, `context`, and `llmRequest`. Persistence and context building
run concurrently. A builder failure skips injection but does not stop
persistence.

`ZepContextTool` provides the same before-model behavior as a `BaseTool`. Use
the tool or the before-model callback, not both, to avoid duplicate user
message persistence.

## Search the graph

`ZepGraphSearchTool` supports `edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, and `auto` scopes. It uses the matching v4 search method.
The `auto` scope calls `graph.getContext`.

Pass `graphUuid` to search a fixed graph. When it is omitted, pass `userUuid`
or provide it in session state. The tool calls `zep.user.get` once for each
user and caches the graph UUID.

```ts
import { ZepGraphSearchTool } from "@getzep/zep-adk";

const searchTool = new ZepGraphSearchTool({
  zep,
  graphUuid,
  scope: "edges",
  limit: 5,
  searchFilters: { edgeTypes: ["LIKES"] },
  bfsOriginNodeUuids: [nodeUuid],
});
```

The tool exposes `scope`, `reranker`, `limit`, `mmrLambda`, and
`centerNodeUuid` to the model when the constructor omits them. A concrete value
pins a parameter and hides it from the model. `null` hides a parameter and
omits it from the search request. `searchFilters` and `bfsOriginNodeUuids` are
constructor-only.

The v4 SDK does not support `nodeLabels` for edge search or filters for
observation search. The tool rejects these filter combinations before it
calls Zep.

## Use ADK's memory service

`ZepMemoryService` implements ADK's `BaseMemoryService`. Register it on a
`Runner` with `LOAD_MEMORY` or `PRELOAD_MEMORY`. Pass `graphUuid` to search a
fixed graph. If you omit it, the ADK `userId` must be the Zep user UUID.

```ts
import { InMemorySessionService, LOAD_MEMORY, Runner } from "@google/adk";
import { ZepMemoryService } from "@getzep/zep-adk";

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-3.7-flash",
  tools: [LOAD_MEMORY],
});
const runner = new Runner({
  agent,
  appName: "my_app",
  sessionService: new InMemorySessionService(),
  memoryService: new ZepMemoryService({ zep, graphUuid }),
});
```

The memory service supports all six search scopes. `addSessionToMemory` is a
no-op because the callbacks already persist each turn.

## Identity

The callbacks use `userUuid` and `threadUuid` options first. They then read
`zep_user_uuid` and `zep_thread_uuid` from ADK session state. The ADK `userId`
and `sessionId` are fallbacks only when they contain Zep UUIDs. The callbacks
do not create resources or call deprecated lookup methods.

The `zep_first_name` and `zep_last_name` session-state keys set the message
author name. Pass the user's email to `createUser`.

## Upgrade from 0.2.x

- Replace `ensureUser` and `ensureThread` with `createUser` and `createThread`.
- Remove caller-selected user and thread IDs. Store the UUIDs returned by Zep.
- Replace `userId` and `threadId` integration options with `userUuid` and
  `threadUuid`.
- Replace `zep_user_id` and `zep_thread_id` state keys with
  `zep_user_uuid` and `zep_thread_uuid`.
- Set `graphUuid` on graph search or the memory service when you use a fixed
  graph. Otherwise provide a Zep `userUuid`.
- Install `@getzep/zep-cloud@^4.0.0`.

## Examples and tests

See [`examples/basic-agent.ts`](examples/basic-agent.ts) and
[`examples/graph-search-agent.ts`](examples/graph-search-agent.ts).

```bash
npm run typecheck
npm test
npm run build
```

The live tests run when `ZEP_API_KEY` is set. They also require
`GOOGLE_API_KEY` for Gemini-backed agent calls.
