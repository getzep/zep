# Setup

This guide walks you from zero to a running Mastra agent with Zep memory.

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
npm install @getzep/zep-mastra @getzep/zep-cloud @mastra/core
```

`@mastra/core` is a peer dependency, so install it alongside the package.

## 4. Configure environment variables

The runnable example uses Zep and an OpenAI model. Set:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

Only `ZEP_API_KEY` is required by the integration itself; `OPENAI_API_KEY` is
needed by the example agent's model (`openai/gpt-4o-mini`). Swap in any model
Mastra supports if you prefer a different provider.

## 5. Run the example

From this package directory:

```bash
npm install
npm run example
```

The example ([`examples/basic-agent.ts`](./examples/basic-agent.ts)):

1. Provisions a Zep user and thread.
2. Builds the Zep tool set and attaches it to a Mastra `Agent`.
3. Seeds a couple of facts, waits ~15s for Zep's asynchronous graph ingestion,
   then asks the agent to recall them.

> Note: Zep ingestion is asynchronous, so the example waits before recalling.
> If your OpenAI organization enforces Zero Data Retention (ZDR), Mastra's
> default OpenAI Responses-API agent loop may be rejected by OpenAI — this is an
> OpenAI/account constraint, not a Zep issue. Use a non-ZDR key or a different
> model provider to run the example end-to-end.

## 6. Run the tests

```bash
npm test
```

Mock-based tests run with no credentials. The live test in
[`test/live.test.ts`](./test/live.test.ts) runs automatically only when
`ZEP_API_KEY` is set and exercises the real Zep API.

## Next steps

- Read [README.md](./README.md) for the full tool reference and the user-graph
  vs standalone-graph binding model.
- See the [Zep documentation](https://help.getzep.com) for ontology, search
  scopes, and context customization.
