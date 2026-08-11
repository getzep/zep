# eve Agent App

This project uses the eve framework with Zep as the long-term memory backend.

Before changing agent files, read the relevant guide under `node_modules/eve/docs/`
(or https://eve.dev/docs). Zep SDK docs: https://help.getzep.com

## Memory architecture (this example)

1. `agent/channels/eve.ts` — `onMessage` stashes the inbound utterance (no channel `context`)
2. `agent/hooks/zep-persist.ts` — on `session.started`, rebind create-session stash → session id; persist messages; `user.warm`
3. `agent/instructions/zep-memory.ts` — `turn.started` dynamic instructions run `graph.search` and replace turn-scoped system memory
4. `agent/tools/zep_search.ts` — on-demand user-graph recall (`scope: "auto"`)
5. `agent/tools/zep_search_company.ts` — on-demand standalone company-graph recall
6. `agent/lib/identity.ts` / `pending-utterance.ts` — identity mapping + in-process utterance stash
7. `scripts/seed-company-graph.ts` — seed the company standalone graph via Zep SDK only

Never accept a Zep `userId` from the model. Pin it from session auth (or the demo env fallback).

Auto-recall belongs in turn-scoped dynamic instructions (replaced each turn), not channel `context` (which accumulates in session history). The utterance stash is single-process only.
