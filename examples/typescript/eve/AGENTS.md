# eve Agent App

This project uses the eve framework with Zep as the long-term memory backend.

Before changing agent files, read the relevant guide under `node_modules/eve/docs/`
(or https://eve.dev/docs). Zep SDK docs: https://help.getzep.com

## Memory architecture (this example)

1. `agent/channels/eve.ts` — retrieve turn-relevant graph context and add it as user-message context
2. `agent/hooks/zep-persist.ts` — provision the user and thread UUIDs, persist messages, and warm the user graph
3. `agent/tools/zep_search.ts` — on-demand user-graph context retrieval
4. `agent/tools/zep_search_company.ts` — on-demand standalone company-graph context retrieval
5. `agent/lib/identity.ts` — map application identities to Zep UUIDs
6. `scripts/seed-company-graph.ts` — seed the company standalone graph via Zep SDK only

Never accept a Zep UUID from the model. Pin the application identity from session auth or the demo env fallback, then use the UUID stored in the identity file.

The app stores returned user and thread UUIDs in `.data/zep-identities.json` by default. Keep this file across runs.

The channel adds retrieved memory as user-message context. Eve keeps the context out of the system prompt.
