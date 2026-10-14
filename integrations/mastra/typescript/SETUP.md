# Setup

This guide explains how to set up Zep and run the Mastra integration.

## Create a Zep API key

1. Create an account at [getzep.com](https://www.getzep.com).
2. Open your Zep project settings.
3. Create an API key and store it securely.

Do not commit the API key or add it to a source file.

## Install the packages

```bash
npm install @getzep/zep-mastra @getzep/zep-cloud @mastra/core
```

The integration requires Node.js 20 or later. `@mastra/core` is a peer
dependency.

## Set environment variables

The example uses Zep and an OpenAI model:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

Only `ZEP_API_KEY` is required by the integration. Set the API key for the
model provider that you use.

## Run the example

From this package directory, install the development dependencies and run:

```bash
npm install
npm run example
```

The example creates a user and thread with server-generated UUIDs. It stores
facts through the Mastra output processor, waits for graph ingestion, and then
asks the agent to recall the facts. Store the returned UUIDs in your
application database for production use. Each example run creates a new user.

## Run checks and tests

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

Mock tests do not require credentials. The live test runs when `ZEP_API_KEY` is
set and deletes the UUID-based test thread and user after the test.

```bash
ZEP_API_KEY="your-zep-api-key" npm test -- test/live.test.ts
```

See [README.md](./README.md) for the API reference and usage examples.
