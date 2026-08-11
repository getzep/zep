# Zep × Eve working example

Standalone Eve agent that uses **Zep Cloud** as long-term memory — hooks + authored tools + turn-scoped dynamic instructions (no `@getzep/zep-eve` package required). Auto-recall is injected into the **system prompt** and replaced each turn.

## What this demonstrates

| Eve primitive | File | Zep API |
| --- | --- | --- |
| Channel `onMessage` (stash utterance) | `agent/channels/eve.ts` | — (records text for the next step) |
| Dynamic instructions (turn-relevant recall) | `agent/instructions/zep-memory.ts` | `graph.search` (`scope: "auto"`, query = current utterance) |
| Hook (auto-persist) | `agent/hooks/zep-persist.ts` | `thread.addMessages`, `user.warm` |
| Authored tool (user search) | `agent/tools/zep_search.ts` | `graph.search` (user graph, `scope: "auto"`) |
| Authored tool (company search) | `agent/tools/zep_search_company.ts` | `graph.search` (standalone graph, `scope: "auto"`) |
| Product tools steered by memory | `call_service_a` / `call_service_b` / `call_capability_x` | — |

Identity mapping:

- Eve `session.id` → Zep `threadId` (`eve-<sessionId>`)
- Eve `session.auth` principal (or `ZEP_DEMO_USER_ID`) → Zep `userId`

## Prerequisites

- **Node.js 24+** (Eve requirement)
- A **Zep Cloud** API key (`ZEP_API_KEY`)
- A **Google Gemini** API key (`GOOGLE_API_KEY`) — used directly via `@ai-sdk/google`

## Setup

```bash
cd examples/typescript/eve
```

```bash
cp .env.example .env
```

Fill in `.env`:

- `ZEP_API_KEY` — required
- `GOOGLE_API_KEY` — required for the default Gemini model
- `AI_GATEWAY_API_KEY` — leave blank unless you switch `agent.ts` to a gateway model id
- `ZEP_DEMO_USER_ID` — demo identity when Eve auth is not configured

```bash
npm install
```

Optional Zep-only smoke test (no Eve runtime). Polls until episodes are processed and fails if context/search stay empty:

```bash
npm run smoke
```

Seed the company standalone graph (required once for `zep_search_company`; re-runs skip if already seeded):

```bash
npm run seed:company
```

## Run the agent

```bash
npm run dev
```

This starts Eve’s local TUI. Try a conversation like:

1. “I prefer Service A over Service B for billing. Please remember that.”
2. **Wait for async graph processing** — open the user in the [Zep app](https://app.getzep.com) and confirm the preference fact appears (often tens of seconds; sometimes longer). Do **not** expect same-session recall before that. Optionally re-run `npm run smoke` after a similar ingest to verify processing.
3. Start a **new** Eve session with the same `ZEP_DEMO_USER_ID` (or continue only after facts are visible), then ask: “Which service should you use for billing?” — the agent should prefer Service A from the turn’s Zep memory section and/or `zep_search`.
4. “Run a refund” — it should call `call_service_a` when preferences are known.

Inspect the Zep user under the project tied to your API key (`users` → `eve-demo-user` by default).

## Architecture (concise)

```text
Eve HTTP message
  │
  ├─ channels/eve onMessage
  │     → stash current utterance (in-process; session id or user queue)
  │       (no channel `context` — that would accumulate in history)
  │
  ├─ session.started → hooks/zep-persist
  │     → rebind create-session stash onto session id
  │     → ensure Zep user/thread + user.warm
  │
  ├─ turn.started → instructions/zep-memory
  │     → peek stashed utterance (clear after search settles)
  │     → graph.search(auto, query=truncated utterance ≤400 chars)
  │     → replace turn-scoped system instructions with Zep facts
  │
  ├─ hooks/zep-persist
  │     message.received / message.completed(stop)
  │     → Zep thread.addMessages
  │
  └─ tools
        zep_search / zep_search_company  (pinned userId / graphId)
        call_service_a / b / capability_x
```

Why the stash: Eve emits `turn.started` (dynamic instructions) before `message.received`, and the turn.started resolver does not receive the inbound text. Channel `onMessage` runs after the HTTP body is parsed, so it records the utterance for the instruction resolver. Turn-scoped instructions replace the previous turn’s block — only the latest recall is in the system prompt.

## Production notes

1. **Replace demo identity** — wire real auth (`Auth.js` / Clerk / etc.) on `agent/channels/eve.ts`, then resolve `userId` from `ctx.session.auth.current.principalId` in `agent/lib/identity.ts`. Remove the `ZEP_DEMO_USER_ID` fallback for multi-tenant production.
2. **Hook failures** — Zep I/O is try/caught so a Zep outage does not fail the Eve turn.
3. **Async indexing** — facts from a turn may not appear in search until processing finishes; confirm in the Zep UI before expecting preference recall.
4. **At-least-once hooks** — Eve may retry; duplicate `addMessages` is usually acceptable for demos. For production, key side effects on turn coordinates if you need stricter dedupe.
5. **Prompt caching** — putting fresh memory in the system prompt breaks the cache prefix each turn (see [Zep’s placement write-up](https://blog.getzep.com/where-you-put-memory-in-the-prompt-can-cut-your-token-bill-up-to-2x/)). Eve does not yet expose replaceable trailing/ephemeral context, so turn-scoped instructions are the supported alternative to accumulating channel `context` / tool-result history.
6. **Utterance stash is in-process** — `onMessage` and `turn.started` must run in the same Node process (`eve dev` / single instance). Multi-isolate hosts (e.g. Vercel Workflow) need an external stash. Create-session has no `sessionId` in `onMessage`; the demo queues by `ZEP_DEMO_USER_ID` and rebinds on `session.started` (FIFO). Avoid concurrent new sessions that share one demo user id.
7. **MCP** — not used here. Prefer authored tools for the in-app agent; Zep Memory MCP is complementary for end-user SSO clients.

## Project layout

```text
examples/typescript/eve/
├── agent/
│   ├── agent.ts
│   ├── instructions.md
│   ├── instructions/zep-memory.ts
│   ├── channels/eve.ts
│   ├── hooks/zep-persist.ts
│   ├── lib/identity.ts
│   ├── lib/pending-utterance.ts
│   ├── lib/zep-client.ts
│   ├── lib/zep-memory.ts
│   ├── lib/zep-recall.ts
│   ├── tools/…
│   └── skills/memory-policy/
├── scripts/smoke-memory.ts
├── scripts/seed-company-graph.ts
├── .env.example
└── package.json
```

## References

- [Searching the graph](https://help.getzep.com/searching-the-graph) (primary API used by this example)
- [Advanced context block construction](https://help.getzep.com/cookbook/advanced-context-block-construction)
- [Eve multi-tenant memory](https://eve.dev/docs/patterns/multi-tenant-memory)
- [Eve dynamic capabilities](https://eve.dev/docs/guides/dynamic-capabilities)
- [Eve hooks](https://eve.dev/docs/guides/hooks)
- [Zep architecture patterns](https://help.getzep.com/architecture-patterns)
- [Zep performance](https://help.getzep.com/performance)
