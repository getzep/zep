# Setup Guide

This guide walks you from a fresh machine to running the example agent with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get an OpenAI API key (for the example)

The integration itself is model-agnostic — it works with any model
[Pydantic AI supports](https://ai.pydantic.dev/models/). The bundled example and
live tests drive the agent with OpenAI. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Install

Using `pip`:

```bash
pip install zep-pydantic-ai
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/pydantic-ai/python
make install        # uv sync --extra dev
```

Requirements: Python 3.11+, `pydantic-ai>=1.107,<2`, `zep-cloud==4.0.0a5`.

`zep-cloud` 4.0.0a5 is a pre-release. To install it with `pip` directly, use
`pip install --pre "zep-cloud==4.0.0a5"`.

The package targets the Zep v4 API. Zep v4 addresses every user, thread, and
graph by a server-generated UUID. Your application creates the user and the
thread one time, stores the returned UUIDs, and passes them to `ZepDeps`.

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

## 5. Run the example

From the repository:

```bash
uv run python examples/basic_agent.py
```

Or, if you installed with `pip`:

```bash
python examples/basic_agent.py
```

The example:

1. Creates a Zep user and a thread, and reads the returned UUIDs.
2. Seeds facts about the user across two turns in one thread.
3. Waits for Zep to process the knowledge graph (ingestion is asynchronous).
4. Asks recall questions — the agent answers using facts fused into the user's
   graph, injected automatically by the history processor.

## 6. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

These include end-to-end wiring tests that run a real Pydantic AI agent against
Pydantic AI's built-in `TestModel` (no LLM API key required) with a mocked Zep
client.

## Troubleshooting

- **`ZepDependencyError` on import** — Pydantic AI is not installed. Run
  `pip install zep-pydantic-ai` (which pulls `pydantic-ai`).
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The example waits ~15s; increase the wait if your
  graph is large or under load.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
- **`PydanticAIDeprecationWarning` about `openai:`** — this is a forward-compat
  notice from Pydantic AI itself, not from this integration. The example runs
  correctly; pin a fully-qualified model string (e.g. `openai-chat:gpt-4o-mini`)
  to silence it.
