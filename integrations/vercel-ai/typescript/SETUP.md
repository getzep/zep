# Setup

This guide walks you from zero to a running Vercel AI SDK call with Zep memory.

## 1. Sign up for Zep

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the Zep dashboard and create (or select) a project.

> Zep is a paid product (it has no meaningful free tier). See the Zep site for
> current plans.

## 2. Create an API key

1. In the Zep dashboard, open your project settings and find the **API Keys**
   section.
2. Create a new API key and copy it. You won't be able to view it again.

## 3. Install

```bash
npm install @getzep/zep-vercel-ai @getzep/zep-cloud ai zod
```

`ai` (the Vercel AI SDK, v6) and `zod` are peer dependencies. Install a model
provider too — the examples use OpenAI:

```bash
npm install @ai-sdk/openai
```

> This package targets **AI SDK v6** (the `v3` middleware/provider interfaces).
> It is not compatible with AI SDK v5.

## 4. Configure environment variables

The runnable examples use Zep and an OpenAI model. Set:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

Only `ZEP_API_KEY` is required by the integration itself; `OPENAI_API_KEY` is
needed by the example's model (`openai("gpt-4o-mini")`). Swap in any provider the
AI SDK supports if you prefer.

## 5. Run the examples

From this package directory:

```bash
npm install
npm run example                 # generate-text.ts (middleware + tools)
npx tsx examples/stream-text.ts # streamText + onFinish persistence
```

The `generate-text` example
([`examples/generate-text.ts`](./examples/generate-text.ts)):

1. Provisions a Zep user and thread.
2. Wraps the model with `createZepMiddleware` (context injection + persistence).
3. Attaches `createZepTools` so the model can search/store explicitly.
4. Seeds facts across a couple of turns, waits ~15s for Zep's asynchronous graph
   ingestion, then asks the agent to recall them.

The `stream-text` example shows the streaming persistence pattern: fetch context
with `getZepContext`, set it as `system`, and persist the completed turn from
`onFinish` with `persistZepTurn` (because middleware `wrapGenerate` does not fire
for `streamText`).

> **OpenAI Zero Data Retention (ZDR) note.** The `generate-text` example uses the
> OpenAI Chat Completions API (`openai.chat("gpt-4o-mini")`) instead of the
> default Responses API. The Responses API references server-persisted item IDs
> across a multi-step tool loop, which ZDR organizations reject ("Items are not
> persisted for Zero Data Retention organizations"). This is an OpenAI/account
> constraint, not a Zep issue — using the Chat Completions API (or a non-ZDR key)
> avoids it.

## 6. Run the tests

```bash
npm test
```

Mock-based tests run with no credentials. The live test in
[`test/live.test.ts`](./test/live.test.ts) runs automatically only when
`ZEP_API_KEY` is set and exercises the real Zep API.

## Next steps

- Read [README.md](./README.md) for the full layer reference and the user-graph
  vs standalone-graph binding model.
- See the [Zep documentation](https://help.getzep.com) for ontology, search
  scopes, and context customization.
