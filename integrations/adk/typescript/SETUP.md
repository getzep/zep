# Setup

This guide explains how to create a Zep API key, install the package, and run
the examples.

## Create a Zep API key

1. Create an account at [getzep.com](https://www.getzep.com).
2. Open the Zep dashboard and create an API key.
3. Store the API key in the application environment.

The Gemini example also needs a Google API key from
[Google AI Studio](https://aistudio.google.com/app/apikey).

## Install the package

```bash
npm install @getzep/zep-adk @getzep/zep-cloud @google/adk
```

For local development, run these commands from
`integrations/adk/typescript`:

```bash
npm install
npm run typecheck
npm test
```

## Configure the environment

```bash
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

The Google API key is required only for Gemini-backed model calls.

Automatic context injection places retrieved content in the system
instruction. Use it only with trusted, application-authored graph content. Use
`ZepGraphSearchTool` or `ZepMemoryService` for user-provided or third-party
content.

## Run an example

```bash
npm run example
```

The example creates a Zep user and thread before the agent runs. It stores the
returned UUIDs in the ADK session state. The example requires both API keys to
run a model call.

The graph-search example shows a model-callable search tool:

```bash
npx tsx examples/graph-search-agent.ts
```

## Run the tests

```bash
npm run typecheck
npm test
npm run build
```

The unit tests use a mocked Zep client. The live tests run when `ZEP_API_KEY`
is set. Gemini-backed tests also require `GOOGLE_API_KEY`.

## Troubleshooting

- `ZEP_API_KEY is not set`: set the Zep API key before running an example.
- `ZepIdentityError`: pass `userUuid` and `threadUuid`, set
  `zep_user_uuid` and `zep_thread_uuid` in session state, or use ADK identifiers
  that are Zep UUIDs.
- No memory appears in a response: Zep processes messages asynchronously.
  Allow time for ingestion before testing retrieval.
