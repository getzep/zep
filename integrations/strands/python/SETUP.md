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
drive the agent with OpenAI via `strands-agents[openai]`. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Install

Using `pip`:

```bash
pip install zep-strands 'strands-agents[openai]'
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/strands/python
make install        # uv sync --extra dev (includes strands-agents[openai])
```

Requirements: Python 3.11+, `strands-agents>=1.45.0`, `zep-cloud>=3.23.0`.

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

1. Seeds facts about a user across two turns in one thread.
2. Waits for Zep to process the knowledge graph (ingestion is asynchronous).
3. Starts a **new** thread for the same user and asks recall questions — the
   agent answers using facts fused into the user's graph from the first thread.

## 6. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

Live integration tests:

```bash
# Store round-trip (ZEP_API_KEY only)
# Full agent lifecycle (also needs OPENAI_API_KEY)
uv run pytest tests/test_integration.py -v -s -m integration

# Or run the agent lifecycle as a standalone script:
uv run python tests/test_integration.py
```

## Troubleshooting

- **`ZepDependencyError` on import** — Strands Agents is not installed. Run
  `pip install zep-strands` (which pulls `strands-agents`).
- **Recall returns nothing** — two delays apply. First, Strands' default
  extraction only sends conversation batches to Zep every **5 turns** (or on
  `memory_manager.flush()`). Second, Zep ingestion is asynchronous after
  messages arrive. The example waits ~20s after seeding; increase the wait or
  flush / use an every-turn trigger if your graph is large or under load.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
- **`extraction=True` / `add_messages` errors about `thread_id`** — user-graph
  mode with extraction requires both `user_id` and `thread_id` at construction.
  Standalone graphs must use `extraction=False` and `add()` only.
