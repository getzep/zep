# Zep × Eve working example

Standalone Eve agent that uses **Zep Cloud** for long-term memory through hooks, authored tools, and turn-scoped dynamic instructions. Auto-recall is added to the system prompt and replaced each turn.

## What this demonstrates

| Eve primitive | File | Zep API |
| --- | --- | --- |
| Channel `onMessage` (stash utterance) | `agent/channels/eve.ts` | Records text for the next step |
| Dynamic instructions (turn-relevant recall) | `agent/instructions/zep-memory.ts` | `graph.getContext` on the user graph UUID |
| Hook (auto-persist) | `agent/hooks/zep-persist.ts` | `thread.addMessages`, `graph.warm` |
| Authored tool (user search) | `agent/tools/zep_search.ts` | `graph.getContext` on the user graph UUID |
| Authored tool (company search) | `agent/tools/zep_search_company.ts` | `graph.getContext` on the standalone graph UUID |
| Product tools steered by memory | `call_service_a` / `call_service_b` / `call_capability_x` | — |

Identity mapping:

- Eve `session.auth` principal (or `ZEP_DEMO_USER_ID`) → identity file → Zep-returned user UUID
- Eve `session.id` → application thread key → identity file → Zep-returned thread UUID

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
- `ZEP_DEMO_USER_ID` — application identity label when Eve auth is not configured
- `ZEP_IDENTITY_FILE` — path for the persisted user and thread UUID mapping
- `ZEP_COMPANY_GRAPH_UUID` — set this to the UUID printed by `npm run seed:company`

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
3. Start a **new** Eve session with the same `ZEP_DEMO_USER_ID` and identity file. Ask: “Which service should you use for billing?” The agent should prefer Service A from the turn's Zep memory section or `zep_search`.
4. “Run a refund” — it should call `call_service_a` when preferences are known.

The app stores Zep-returned user and thread UUIDs in `.data/zep-identities.json` by default. Keep this file across runs. The application identity label is not sent as a Zep address.

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
  │     → ensure Zep user/thread UUIDs; warm the user graph
  │
  ├─ turn.started → instructions/zep-memory
  │     → peek stashed utterance (clear after search settles)
  │     → graph.getContext(user UUID, query=truncated utterance ≤400 chars)
  │     → replace turn-scoped system instructions with Zep facts
  │
  ├─ hooks/zep-persist
  │     message.received / message.completed(stop)
  │     → Zep thread.addMessages
  │
  └─ tools
        zep_search / zep_search_company  (pinned user UUID / graph UUID)
        call_service_a / b / capability_x
```

Eve emits `turn.started` before `message.received`, and the turn resolver does not receive the inbound text. Channel `onMessage` runs after the HTTP body is parsed, so it records the utterance for the instruction resolver. Turn-scoped instructions replace the previous turn's block. The recall does not accumulate in session history.

## Production notes

1. **Replace demo identity** — wire real auth on `agent/channels/eve.ts`, then resolve the application identity from `ctx.session.auth.current.principalId`. The identity file maps that value to a Zep-returned UUID.
2. **Hook failures** — Zep I/O is try/caught so a Zep outage does not fail the Eve turn.
3. **Async indexing** — facts from a turn may not appear in search until processing finishes; confirm in the Zep UI before expecting preference recall.
4. **At-least-once hooks** — Eve may retry; duplicate `addMessages` is usually acceptable for demos. For production, key side effects on turn coordinates if you need stricter dedupe.
5. **Prompt caching** — fresh memory changes the system prompt prefix each turn. Eve replaces the turn-scoped instruction block instead of accumulating channel `context` or tool-result history.
6. **Utterance stash is in-process** — `onMessage` and `turn.started` must run in the same Node process (`eve dev` / single instance). Multi-isolate hosts need an external stash. Create-session has no `sessionId` in `onMessage`; the demo queues by application user identity and rebinds on `session.started` (FIFO).
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
