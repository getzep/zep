# Zep Memory for DeepSeek Harness

`@getzep/zep-deepseek-harness` adds durable long-term memory to
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).

It uses the Harness's native lifecycle:

- `agent/pre-step` retrieves the user's Zep Context Block on each genuine user
  turn and adds it as a source-attributed, durable `user/message`.
- `session/event` persists direct user input and the final assistant response
  after a successful `turn/end`.
- Zep user and thread resources are provisioned lazily and idempotently.
- Zep errors fail open: they are logged without conversation content and never
  stop the agent loop.

The injected Context Block is intentionally logged. DeepSeek Harness requires
all model-visible input to be reconstructable from the session log; modifying a
model request directly would violate that invariant.

## Install

DeepSeek Harness plugins are installed into a profile:

```bash
dsh plugin --profile headless add @getzep/zep-deepseek-harness
```

Set the values read by the bundled patch:

```bash
export ZEP_API_KEY="your-zep-api-key"
export ZEP_USER_ID="stable-user-id"
```

The bundle adds this row:

```yaml
- insert:
    - id: zep-memory
      name: '@getzep/zep-deepseek-harness'
      config:
        apiKey: !!js process.env.ZEP_API_KEY
        userId: !!js process.env.ZEP_USER_ID
```

See [SETUP.md](SETUP.md) for the complete setup.

## Behavior

### Recall

Recall happens once at the start of a genuine user turn. Tool-loop steps and
plugin-generated context do not trigger additional retrieval. The plugin calls
`thread.getUserContext`, formats the Context Block, and appends it to the
downstream pre-step decision with this source:

```ts
{
  kind: "plugin",
  plugin: "zep-memory",
  form: "snapshot"
}
```

This keeps memory visible to the model, session replay, compaction, and UI
consumers without pretending that it was human input.

### Persistence

On a completed or max-token turn, the plugin sends one batch to
`thread.addMessages` containing:

1. Direct human messages from the turn.
2. The final assistant text from the turn.

Injected contexts, tool results, reasoning blocks, and intermediate tool-call
preambles are excluded. Duplicate `turn/end` notifications for the same live
session do not cause duplicate writes.

Zep graph ingestion is asynchronous. Memory written in one turn may not be
available immediately in the next turn.

## Configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `apiKey` | `string` | required | Zep Cloud API key. |
| `userId` | `string` | required | Stable Zep user id. |
| `threadId` | `string` | Harness session id | Fixed thread id. Use only when this plugin instance serves one session. |
| `threadIdPrefix` | `string` | `""` | Prefix for session-derived thread ids. |
| `firstName` | `string` | — | User identity metadata. |
| `lastName` | `string` | — | User identity metadata. |
| `email` | `string` | — | User identity metadata. |
| `userMessageName` | `string` | — | Name on persisted user messages. |
| `assistantMessageName` | `string` | `"Assistant"` | Name on persisted assistant messages. |
| `contextTemplate` | `string` | `Relevant long-term memory from Zep:\n\n{context}` | Injected context wrapper; must contain `{context}` exactly once. |
| `contextTemplateId` | `string` | — | Custom Zep Context Block template id. |
| `recall` | `boolean` | `true` | Enable Context Block retrieval. |
| `persist` | `boolean` | `true` | Enable completed-turn persistence. |

By default, every Harness session gets a separate Zep thread under the same
user graph. This preserves conversational relevance while allowing recall from
the user's whole graph. Set `threadId` only for a deployment that deliberately
maps the plugin to one conversation.

## Programmatic use

When another plugin already owns a shared `ZepClient`, install the listeners
without creating another client:

```ts
import { ZepClient } from "@getzep/zep-cloud";
import {
  ZepMemoryRuntime,
  installZepMemory,
} from "@getzep/zep-deepseek-harness";

const runtime = new ZepMemoryRuntime({
  client: new ZepClient({ apiKey: process.env.ZEP_API_KEY! }),
  userId: "user-123",
  firstName: "Ada",
  lastName: "Lovelace",
});

installZepMemory(ctx, runtime);
```

The caller owns the supplied client. Listener registrations and pending-write
drain are attached to the Cordis plugin lifecycle.

## Development

```bash
npm install
npm run lint
npm run typecheck
npm test
npm run build
```

## Compatibility

- DeepSeek Harness `0.1.0-rc.6`
- Zep Cloud JavaScript SDK `3.28.0`
- Node.js `^22.19.0 || >=24`

DeepSeek Harness is in developer preview and may make breaking plugin API
changes. This package pins its tested release-candidate range accordingly.
