# Setup Guide

This guide walks you from a fresh machine to running the example LangGraph agent
with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get an OpenAI API key (for the example)

The integration itself is model-agnostic — it works with any chat model
[LangChain supports](https://python.langchain.com/docs/integrations/chat/). The
bundled `react_agent.py` example drives the agent with OpenAI. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Install

Using `pip`:

```bash
pip install zep-langgraph
# For the react_agent.py example's model:
pip install langchain-openai
```

This package targets the Zep v4 SDK, which is a pre-release. To install the
SDK on its own, use `pip install --pre "zep-cloud==4.0.0a5"` or
`uv add "zep-cloud==4.0.0a5"`.

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/langgraph/python
make install        # uv sync --extra dev
```

Requirements: Python 3.11+, `langgraph>=1.2.5`, `zep-cloud==4.0.0a5`.
`langgraph` pulls in `langchain-core`, which provides the message and tool types
this package uses.

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

The SDK sends requests to the Zep v4 API at `https://api.getzep.com/api/v4` by
default. Pass `base_url` to `Zep` or `AsyncZep` only if you must use a
different endpoint.

## 4a. Identifiers in Zep v4

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
create call does not take an application identifier: it returns the object,
and you read `User.uuid_`, `User.graph_uuid`, and `Thread.uuid_` from the
response. Store those UUIDs in your own database and pass them to the
integration on every turn. The integration never resolves an application
identifier at run time.

## 5. Run the example

The primary example wires Zep into `create_react_agent` (context injection +
graph-search tool + per-turn persistence). From the repository:

```bash
uv run python examples/react_agent.py
```

Or, if you installed with `pip` (and `langchain-openai`):

```bash
python examples/react_agent.py
```

The example:

1. Creates a Zep user and a thread, and reads their UUIDs from the responses.
2. Seeds facts about the user across two turns.
3. Waits for Zep to build the knowledge graph (ingestion is asynchronous).
4. Asks recall questions — the agent answers using facts fused into the user's
   graph, injected automatically into the system prompt.

> **Note (ZEPAI-3605):** a user that is created without a `user_id` cannot
> receive a thread message until the fix is deployed. Until then, this example
> cannot complete its recall step.

To see the secondary `ZepStore` path (a `BaseStore` backed by Zep), run:

```bash
uv run python examples/store_agent.py   # only needs ZEP_API_KEY
```

That example creates a standalone graph and gives `ZepStore` the returned
`Graph.uuid_`.

## 6. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

These cover the context, persistence, and tool helpers, plus the `ZepStore`
contract (it is a `BaseStore`, its `__abstractmethods__` is empty, KV operations
delegate to the backing store, `put` ingests into Zep, and `search` routes to
Zep semantic search) — all with a mocked Zep client.

## Troubleshooting

- **`ZepDependencyError` on import** — LangGraph / LangChain are not installed.
  Run `pip install zep-langgraph` (which pulls `langgraph` and `langchain-core`).
- **`ModuleNotFoundError: langchain_openai`** — install the example's model
  provider with `pip install langchain-openai`.
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The example waits ~15s; increase the wait if your
  graph is large or under load. The same applies to `ZepStore.search` after a
  `put`.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
