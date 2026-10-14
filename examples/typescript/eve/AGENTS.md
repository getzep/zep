# eve Agent App

This project uses the eve framework with Zep as the long-term memory backend.

Before changing agent files, read the relevant guide under `node_modules/eve/docs/`
(or https://eve.dev/docs). Zep SDK docs: https://help.getzep.com

## Memory architecture (this example)

1. `agent/channels/eve.ts` — stash the inbound utterance for the next turn
2. `agent/hooks/zep-persist.ts` — rebind the create-session stash, persist messages, and warm the user graph
3. `agent/instructions/zep-memory.ts` — run `graph.getContext` on `turn.started` and replace turn-scoped system memory
4. `agent/tools/zep_search.ts` — on-demand user-graph recall with the user UUID
5. `agent/tools/zep_search_company.ts` — on-demand standalone company-graph recall with the graph UUID
6. `agent/lib/identity.ts` / `agent/lib/pending-utterance.ts` — map application identities to returned UUIDs and bridge the utterance to the turn
7. `scripts/seed-company-graph.ts` — seed the company standalone graph via Zep SDK only

Never accept a Zep UUID from the model. Pin the application identity from session auth or the demo environment fallback. Use the returned user and thread UUIDs stored in the identity file for Zep calls.

The app stores returned user and thread UUIDs in `.data/zep-identities.json` by default. Keep this file across runs.

Eve emits `turn.started` before `message.received`, and the turn resolver does not include the inbound text. The channel stashes the text for the dynamic instruction resolver. Dynamic instructions replace the previous turn's memory block, so it does not accumulate in session history. Keep the stash and resolver in the same process.
