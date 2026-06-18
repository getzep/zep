# Zep LangGraph Integration

Give [LangGraph](https://github.com/langchain-ai/langgraph) agents durable,
cross-session memory backed by [Zep](https://www.getzep.com)'s temporal Context
Graph. The package ships two layers:

- **Node / tool helpers (primary)** — call Zep directly inside your graph nodes:
  inject the user's Context Block into the system prompt, persist each turn, and
  expose a graph-search tool. This matches Zep's own LangGraph guide.
- **`ZepStore` (secondary)** — a hybrid-delegate
  [`BaseStore`](https://langchain-ai.github.io/langgraph/reference/store/) for
  `create_react_agent(store=...)` and langmem's memory tools.

## Installation

```bash
pip install zep-langgraph
```

See [SETUP.md](SETUP.md) for creating a Zep account, getting an API key, and
running the example end to end.

## Quick Start (primary path)

Inject Zep context with a `prompt` callable, expose a graph-search tool, and
persist each turn. Identity (the user, thread, and the user's real name) is yours
to manage — create the Zep user and thread out-of-band before the first turn.

```python
import os
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from zep_cloud import Message
from zep_cloud.client import AsyncZep
from zep_langgraph import build_system_message, create_graph_search_tool, persist_messages

zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

async def prompt(state):
    system = await build_system_message(
        zep, thread_id="thread-1", base_instructions="You are a helpful assistant."
    )
    return [system, *state["messages"]]

agent = create_react_agent(
    model=ChatOpenAI(model="gpt-5"),
    tools=[create_graph_search_tool(zep, user_id="user-1")],
    prompt=prompt,
)

result = await agent.ainvoke({"messages": [HumanMessage(content="Where do I work?")]})
reply = result["messages"][-1]
await persist_messages(
    zep,
    thread_id="thread-1",
    messages=[Message(role="user", content="Where do I work?", name="Alice Smith"), reply],
)
```

A complete runnable version is in
[examples/react_agent.py](examples/react_agent.py).

## How It Works

The Zep loop is the same everywhere: **create user → create thread → add
messages → retrieve context**. This package wraps each step as a helper you call
from inside a graph node.

### Context injection — `build_system_message` / `get_zep_context`

`thread.get_user_context(thread_id)` returns a token-efficient **Context Block**
assembled from the *entire user graph* (the thread only scopes what is relevant
right now). `build_system_message` fetches it and folds it into a
`SystemMessage` together with your base instructions, ready to prepend to the
model's message list. `get_zep_context` returns just the raw block.

Implemented in [src/zep_langgraph/context.py](src/zep_langgraph/context.py).

### Persistence — `persist_messages`

Wraps `thread.add_messages`. Accepts LangChain `BaseMessage` objects (converted
automatically — `human`→`user`, `ai`→`assistant`, …) or native Zep `Message`
objects, flattens multimodal content to text, truncates over-long messages, and
maps names so Zep can resolve identity. Pass `return_context=True` to fold
persist + retrieve into one round-trip.

Implemented in
[src/zep_langgraph/persistence.py](src/zep_langgraph/persistence.py).

### On-demand search — `create_graph_search_tool`

Returns a LangChain `StructuredTool` over `graph.search`. Bind it to a model or
pass it to `create_react_agent(tools=[...])` and the model decides when to search
the graph. The target (`user_id` for a personal graph, `graph_id` for a shared
standalone graph) and the search parameters (`scope`, `reranker`, `limit`) are
fixed at construction so the model only supplies the query.

Implemented in [src/zep_langgraph/tools.py](src/zep_langgraph/tools.py).

### `ZepStore` — a `BaseStore` for the langmem audience

`BaseStore` is LangGraph's cross-thread long-term-memory interface;
`create_react_agent(store=...)` and langmem's
`create_manage_memory_tool` / `create_search_memory_tool` require one. Zep is a
temporal knowledge graph, not a KV store, so `ZepStore` uses a **hybrid-delegate**
design: a backing KV `BaseStore` (default `InMemoryStore`) serves exact-key
`get` / `put` / `delete` / `list_namespaces` faithfully and synchronously, while
every `put` is *also* ingested into Zep and `search` is routed to Zep's semantic
`graph.search`. Only the two abstract methods (`batch` / `abatch`) are
implemented; everything else is inherited and delegates to them.

```python
from zep_langgraph import ZepStore

store = ZepStore(zep)                  # default backing store: InMemoryStore
await store.aput(("memories", "user-1"), "m1", {"text": "Alice works at Acme."})
item = await store.aget(("memories", "user-1"), "m1")     # exact-key, synchronous
hits = await store.asearch(("memories", "user-1"), query="where does Alice work?")
```

> **Zep ingestion is asynchronous.** A value written with `put` is available
> immediately for exact-key `get` (served by the backing store), but its
> extracted facts are **not** instantly returned by `search` — there is no
> read-after-write of graph facts within a turn. `ZepStore` is the long-term
> memory layer, not the checkpointer, so graph execution and short-term state are
> unaffected.

Implemented in [src/zep_langgraph/store.py](src/zep_langgraph/store.py); see
[examples/store_agent.py](examples/store_agent.py).

## Public API

| Symbol | Kind | Purpose |
|--------|------|---------|
| `get_zep_context` / `get_zep_context_sync` | async / sync fn | Fetch the Context Block for a thread |
| `build_system_message` / `build_system_message_sync` | async / sync fn | Build a `SystemMessage` with the Context Block |
| `format_context_block` | fn | Combine base instructions with a Context Block |
| `persist_messages` / `persist_messages_sync` | async / sync fn | Persist a turn (LangChain or Zep messages) |
| `to_zep_message` / `to_zep_messages` | fn | Convert LangChain messages to Zep messages |
| `create_graph_search_tool` / `create_graph_search_tool_sync` | fn | Build a `graph.search` `StructuredTool` |
| `ZepStore` | class | Hybrid-delegate `BaseStore` |

Both an `AsyncZep` (async helpers, recommended) and a synchronous `Zep` client
are supported. Reuse a single client instance.

## Error Handling

Every helper handles Zep failures gracefully: context retrieval and persistence
log a warning and return `None`/an empty result, the search tool returns an error
string, and `ZepStore` keeps serving KV operations from its backing store. **A
Zep failure never crashes the host agent.**

## Configuration

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"   # for the example's model
```

## Examples

- [examples/react_agent.py](examples/react_agent.py) — `create_react_agent` with
  Zep context injection, the graph-search tool, and per-turn persistence.
- [examples/store_agent.py](examples/store_agent.py) — `ZepStore` as a
  `BaseStore`, showing the KV round-trip and Zep-routed semantic search.

## Development

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/langgraph/python
make install      # uv sync --extra dev
make format       # ruff format .
make lint         # ruff check .
make type-check   # mypy src/
make test         # pytest tests/ -v
make all          # all of the above
make build        # uv build
```

## Requirements

- Python 3.11+
- `zep-cloud>=3.23.0`
- `langgraph>=1.2.5` (pulls in `langchain-core`)

## Support

- [Zep Documentation](https://help.getzep.com)
- [Zep LangGraph Guide](https://help.getzep.com/langgraph-memory)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](../../../LICENSE) for details.

## Contributing

Contributions are welcome! Please see our
[Contributing Guide](../../../CONTRIBUTING.md) for details.
