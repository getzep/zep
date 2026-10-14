# Zep × Eve working example

Standalone Eve agent that uses **Zep Cloud** for agent memory. The Eve channel adds retrieved memory as user-message context.

## What this demonstrates

| Eve primitive | File | Zep API |
| --- | --- | --- |
| Channel `onMessage` (turn-relevant recall) | `agent/channels/eve.ts` | `graph.getContext` with the current utterance |
| Hook (auto-persist) | `agent/hooks/zep-persist.ts` | `thread.addMessages`, `graph.warm` |
| Authored tool (user memory) | `agent/tools/zep_search.ts` | `graph.getContext` on the user graph |
| Authored tool (company memory) | `agent/tools/zep_search_company.ts` | `graph.getContext` on the company graph |
| Product tools steered by memory | `call_service_a` / `call_service_b` / `call_capability_x` | — |

Identity mapping:

- Eve application identity → persisted Zep user and thread UUIDs
- Eve `session.auth` principal (or `ZEP_DEMO_USER_ID`) → local identity mapping → Zep-returned user UUID

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
3. Start a **new** Eve session with the same `ZEP_DEMO_USER_ID` and identity file. Ask: “Which service should you use for billing?” The agent should prefer Service A from retrieved memory or `zep_search`.
4. “Run a refund” — it should call `call_service_a` when preferences are known.

The app stores Zep-returned user and thread UUIDs in `.data/zep-identities.json` by default. Keep this file across runs. The application identity label is not sent as a Zep address.

## Architecture (concise)

```text
Eve HTTP message
  │
  ├─ channels/eve onMessage
  │     → resolve the Zep user UUID and retrieve relevant graph context
  │     → add retrieved context as a user message
  │
  ├─ session.started → hooks/zep-persist
  │     → create or read returned UUIDs from the identity file; warm the user graph
  │
  ├─ hooks/zep-persist
  │     message.received / message.completed(stop)
  │     → Zep thread.addMessages
  │
  └─ tools
        zep_search / zep_search_company  (pinned user UUID / graph UUID)
        call_service_a / b / capability_x
```

The channel adds retrieved memory to the user message. The agent treats the memory as untrusted data.

## Production notes

1. **Replace demo identity** — wire real auth on `agent/channels/eve.ts`, then resolve the application identity from `ctx.session.auth.current.principalId`. The identity file maps that value to a Zep-returned UUID.
2. **Hook failures** — Zep I/O is try/caught so a Zep outage does not fail the Eve turn.
3. **Async indexing** — facts from a turn may not appear in search until processing finishes; confirm in the Zep UI before expecting preference recall.
4. **At-least-once hooks** — Eve may retry; duplicate `addMessages` is usually acceptable for demos. For production, key side effects on turn coordinates if you need stricter dedupe.
5. **User-message context** — the channel adds retrieved memory as user-message context. The context is not part of the system prompt.
6. **MCP** — not used here. Prefer authored tools for the in-app agent; Zep Memory MCP is complementary for end-user SSO clients.

## Project layout

```text
examples/typescript/eve/
├── agent/
│   ├── agent.ts
│   ├── instructions.md
│   ├── channels/eve.ts
│   ├── hooks/zep-persist.ts
│   ├── lib/identity.ts
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
- [Eve hooks](https://eve.dev/docs/guides/hooks)
- [Zep architecture patterns](https://help.getzep.com/architecture-patterns)
- [Zep performance](https://help.getzep.com/performance)
