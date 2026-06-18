# Setup Guide

This guide walks you from a fresh machine to running the example agent with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get an OpenAI API key (for the example)

The integration itself is model-agnostic, but the bundled example and live tests
drive the agent with OpenAI. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Install

Using `pip`:

```bash
pip install zep-ms-agent-framework agent-framework-openai
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/ms-agent-framework/python
make install        # uv sync --extra dev (includes agent-framework-openai)
```

Requirements: Python 3.11+, `agent-framework-core>=1.8.1`, `zep-cloud>=3.23.0`.

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"

# Optional: override the OpenAI model used by the example (default: gpt-4o-mini)
export OPENAI_MODEL="gpt-4o-mini"
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

1. Seeds facts about a user across two turns in one thread.
2. Waits for Zep to process the knowledge graph (ingestion is asynchronous).
3. Starts a **new** thread for the same user and asks recall questions — the
   agent answers using facts fused into the user's graph from the first thread.

## 6. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

Live integration test (requires `ZEP_API_KEY` and `OPENAI_API_KEY`):

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

## Troubleshooting

- **`ZepDependencyError` on import** — Microsoft Agent Framework is not
  installed. Run `pip install zep-ms-agent-framework` (which pulls
  `agent-framework-core`).
- **`ModuleNotFoundError: agent_framework.openai`** — install the model
  provider used by the example: `pip install agent-framework-openai`.
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The example waits ~20s; increase the wait if
  your graph is large or under load.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
