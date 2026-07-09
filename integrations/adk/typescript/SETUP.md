# Setup

How to sign up for Zep, create an API key, install this package, configure your environment, and run the example.

## 1. Create a Zep account

1. Go to [https://www.getzep.com](https://www.getzep.com) and sign up.
2. Complete onboarding to reach the Zep dashboard.

Zep is a paid product (no meaningful free tier); see the pricing on the site for current plans.

## 2. Create an API key

1. In the Zep dashboard, open **API Keys** (under project/account settings).
2. Create a new key and copy it — it is shown only once.

## 3. Get a Google API key (for the Gemini model)

The example agent uses a Gemini model, so a **live run** needs a Google API key:

1. Create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Copy the key.

You only need this to run the model. The Zep integration wiring (persisting messages, injecting context) works with any ADK-supported model — swap `model:` in the example for your provider.

## 4. Install

From your project (the package and its peers):

```bash
npm install @getzep/zep-adk @google/adk @getzep/zep-cloud
```

To work on this package from a checkout of the repo:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/adk/typescript
npm install
```

## 5. Configure environment

```bash
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"   # required only for a live model run
```

## 6. Run the example

```bash
npm run example
# or, directly:
npx tsx examples/basic-agent.ts
```

Before its first turn, the example provisions the Zep user and thread out-of-band with `ensureUser` / `ensureThread` — the callbacks and tools never create them implicitly. When wiring your own agent, call `ensureUser` / `ensureThread` once (e.g. during account or session onboarding) before the first turn.

- **With `GOOGLE_API_KEY` set:** the example seeds facts about a user, waits for Zep to process the graph, then asks recall questions and prints the agent's memory-aware answers.
- **Without `GOOGLE_API_KEY`:** the example still creates the Zep user and thread and builds the fully-wired agent, then exits before the model call — useful for verifying the integration end-to-end without a model.

A second example, [`examples/graph-search-agent.ts`](examples/graph-search-agent.ts), wires `ZepContextTool` together with the model-callable `ZepGraphSearchTool`.

## 7. Verify your install

```bash
npm run typecheck   # tsc --noEmit
npm test            # vitest (mocked Zep client — no API key needed)
npm run build       # tsup → dist
```

## Troubleshooting

- **`ZEP_API_KEY is not set`** — export the key (step 5) before running.
- **No memory in responses** — Zep ingestion is asynchronous; a fact added this turn is not retrievable immediately. The example waits 15s before testing recall.
- **`ZepIdentityError`** — pass `userId` / `threadId` to the callback or tool, set `zep_user_id` / `zep_thread_id` in ADK session state, or create the ADK session with a `userId` and `sessionId`.
