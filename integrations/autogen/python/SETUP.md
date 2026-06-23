# Setup Guide

This guide walks you from a fresh machine to running the example agent with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get an OpenAI API key (for the example)

The integration itself is model-agnostic, but the bundled examples and live
tests drive the agent with OpenAI. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Install

Using `pip`:

```bash
pip install zep-autogen
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/autogen/python
make install        # uv sync --extra dev
```

Requirements: Python 3.11+, `autogen-agentchat>=0.7.0`,
`autogen-ext[azure,openai]>=0.7.0`, `zep-cloud>=3.23.0`.

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"

# Optional: override the OpenAI model used by the live test (default: gpt-4o-mini)
export OPENAI_MODEL="gpt-4o-mini"
```

## 5. Run the example

From the repository:

```bash
uv run python examples/autogen_basic.py
```

Or, if you installed with `pip`:

```bash
python examples/autogen_basic.py
```

Other runnable examples live in [`examples/`](examples):

- `autogen_basic.py` — `ZepUserMemory` with automatic context injection
- `autogen_graph.py` — knowledge-graph memory with a custom ontology
- `autogen_tools_search.py` — read-only graph search tool
- `autogen_tools_full.py` — search + add graph data tools

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

- **`ZepDependencyError` on import** — AutoGen is not installed. Run
  `pip install zep-autogen` (which pulls `autogen-agentchat` and `autogen-ext`).
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The live test waits for the graph to build;
  increase the wait if your graph is large or under load.
- **Raw tool output instead of natural language** — set `reflect_on_tool_use=True`
  on the agent when using the graph tools.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
