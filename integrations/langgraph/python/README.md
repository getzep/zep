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

The block is wrapped using `DEFAULT_CONTEXT_TEMPLATE` -- an explicit
`<ZEP_CONTEXT>...</ZEP_CONTEXT>` block, canonical across zep-adk's Python, Go,
and TypeScript implementations -- rendered via plain string replacement
(`template.replace("{context}", context)`, never `str.format`), so context text
or a custom `template` containing `{`, `}`, or `%` is always safe to inject.
Pass `template=...` to customize the wording; it must contain a literal
`{context}` placeholder.

Implemented in [src/zep_langgraph/context.py](src/zep_langgraph/context.py).

#### Custom context building — `context_builder`

Pass `context_builder` to `get_zep_context` / `build_system_message` (or their
`_sync` twins) to *replace* the default `thread.get_user_context` retrieval
with custom logic -- for example, searching a different graph, applying
filters, or combining multiple sources:

```python
from zep_langgraph import ContextInput, build_system_message

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

system = await build_system_message(
    zep, thread_id="thread-1",
    context_builder=my_builder,
    user_id="user-1",
    user_message=state["messages"][-1].content,
)
```

`ContextInput` bundles `zep`, `user_id`, `thread_id`, and `user_message` --
these helpers are plain functions with no surrounding framework object, so
`ContextInput` carries only the Zep call inputs (contrast with the
framework-hooked ports, whose `ContextInput` also carries a tool/run context).
A builder that raises is logged and treated as returning `None` -- these
helpers never raise.

**Running persistence and context building in parallel.** Because these are
plain functions, not a single framework-owned turn hook, this package does not
run persistence and the builder concurrently for you. If your node wants that
overlap, gather both yourself:

```python
import asyncio
from zep_langgraph import build_system_message, persist_messages

async def agent_node(state):
    system, _ = await asyncio.gather(
        build_system_message(
            zep, thread_id="thread-1", context_builder=my_builder,
            user_id="user-1", user_message=state["messages"][-1].content,
        ),
        persist_messages(zep, thread_id="thread-1", messages=[state["messages"][-1]]),
    )
    response = await llm.ainvoke([system, *state["messages"]])
    await persist_messages(zep, thread_id="thread-1", messages=[response])
    return {"messages": [response]}
```

### Persistence — `persist_messages`

Wraps `thread.add_messages`. Accepts LangChain `BaseMessage` objects (converted
automatically — `human`→`user`, `ai`→`assistant`, …) or native Zep `Message`
objects, flattens multimodal content to text, truncates over-long messages, and
maps names so Zep can resolve identity. Pass `return_context=True` to fold
persist + retrieve into one round-trip.

Implemented in
[src/zep_langgraph/persistence.py](src/zep_langgraph/persistence.py).

### Provisioning — `ensure_user` / `ensure_thread`

Explicitly provision the Zep user and thread out-of-band, before the first
turn -- useful for onboarding flows that want genuine failures (auth, network,
5xx) to raise loudly rather than degrade silently:

```python
from zep_langgraph import ensure_thread, ensure_user

async def setup_user(zep_client, user_id: str) -> None:
    ...  # e.g. configure per-user ontology

created = await ensure_user(
    zep,
    user_id="user-1",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,  # fires exactly once, only on real creation
)
await ensure_thread(zep, thread_id="thread-1", user_id="user-1")
```

Synchronous twins `ensure_user_sync` / `ensure_thread_sync` take a synchronous
`Zep` client and a synchronous `on_created` hook. Both are
create-then-catch-conflict: they call the Zep SDK's create method directly and
treat an "already exists" conflict as success (returning `False`), while
genuine failures propagate. If `on_created` raises, that exception also
propagates even though the user was created -- make the hook idempotent so it
can be safely re-run. These are plain module-level functions with no instance
caching -- cache the "already provisioned" result yourself if you want to skip
redundant calls.

Unlike the framework-hooked ports, this package's node helpers (`persist_messages`,
`get_zep_context`) never provision resources lazily -- create the user and
thread yourself, with `ensure_user`/`ensure_thread` or otherwise, before the
first turn.

### On-demand search — `create_graph_search_tool`

Returns a LangChain `StructuredTool` over `graph.search`. Bind it to a model or
pass it to `create_react_agent(tools=[...])` and the model decides when to search
the graph. The target (`user_id` for a personal graph, `graph_id` for a shared
standalone graph) is fixed at construction.

**Pin-or-expose.** Every `graph.search` parameter (`scope`, `reranker`,
`limit`, `mmr_lambda`, `center_node_uuid`) is exposed to the model in the
tool's schema by default, with documented defaults. Use `pinned_params` to fix
a parameter to a constant value and hide it from the schema; use
`hidden_params` to hide a parameter *without* pinning it, so Zep's own
server-side default applies:

```python
from zep_langgraph import create_graph_search_tool

# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_graph_search_tool(zep, user_id="user-1")

# Pin scope to "nodes" and limit to 5 -- hidden from the model, always sent.
tool = create_graph_search_tool(
    zep, user_id="user-1", pinned_params={"scope": "nodes", "limit": 5}
)

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_graph_search_tool(zep, user_id="user-1", hidden_params={"mmr_lambda"})
```

`search_filters` and `bfs_origin_node_uuids` are always constructor-only
(their complex shapes are not exposed to the model). The schema is built
dynamically with `pydantic.create_model` and passed as the `StructuredTool`'s
`args_schema`.

Implemented in [src/zep_langgraph/tools.py](src/zep_langgraph/tools.py).

### Guaranteed context injection — `create_zep_pre_model_hook`

`create_react_agent`'s `prompt=` callable (used in the Quick Start above) is
the recommended way to shape the model's input, but nothing stops a caller
from omitting it. `create_zep_pre_model_hook` builds a
[`pre_model_hook`](https://langchain-ai.github.io/langgraph/reference/agents/#langgraph.prebuilt.chat_agent_executor.create_react_agent)
for `create_react_agent` that guarantees context is injected on every model
call, without relying on the caller wiring a `prompt`:

```python
from langgraph.prebuilt import create_react_agent
from zep_langgraph import create_zep_pre_model_hook

agent = create_react_agent(
    model=model,
    tools=[create_graph_search_tool(zep, user_id="user-1")],
    pre_model_hook=create_zep_pre_model_hook(
        zep, user_id="user-1", thread_id="thread-1",
        base_instructions="You are a helpful assistant.",
    ),
)
```

The hook fetches the Context Block (or runs a custom `context_builder`) and
returns it via the hook's `llm_input_messages` key -- per
`create_react_agent`'s documented `pre_model_hook` contract, this shapes the
input to the model for that step **without** overwriting the persisted
`messages` state, so injected context is re-fetched fresh every turn rather
than baked into thread history. The hook only injects context; call
`persist_messages` separately (e.g. in your agent node, after the model
responds) to save the turn.

Implemented in [src/zep_langgraph/hooks.py](src/zep_langgraph/hooks.py).

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
| `get_zep_context` / `get_zep_context_sync` | async / sync fn | Fetch the Context Block for a thread (or run a `context_builder`) |
| `build_system_message` / `build_system_message_sync` | async / sync fn | Build a `SystemMessage` with the Context Block |
| `format_context_block` | fn | Combine base instructions with a Context Block |
| `ContextInput` / `ContextBuilder` / `ContextBuilderSync` | dataclass / type alias | Custom context-builder contract |
| `DEFAULT_CONTEXT_TEMPLATE` | constant | Canonical `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper |
| `persist_messages` / `persist_messages_sync` | async / sync fn | Persist a turn (LangChain or Zep messages) |
| `to_zep_message` / `to_zep_messages` | fn | Convert LangChain messages to Zep messages |
| `ensure_user` / `ensure_user_sync` | async / sync fn | Idempotently provision a Zep user, out-of-band |
| `ensure_thread` / `ensure_thread_sync` | async / sync fn | Idempotently provision a Zep thread, out-of-band |
| `UserSetupHook` / `UserSetupHookSync` | type alias | `on_created` hook signatures for `ensure_user`(`_sync`) |
| `create_graph_search_tool` / `create_graph_search_tool_sync` | fn | Build a pin-or-expose `graph.search` `StructuredTool` |
| `create_zep_pre_model_hook` | fn | Build a `create_react_agent(pre_model_hook=...)` for guaranteed context injection |
| `ZepStore` | class | Hybrid-delegate `BaseStore` |

Both an `AsyncZep` (async helpers, recommended) and a synchronous `Zep` client
are supported. Reuse a single client instance.

## Error Handling

Every node helper handles Zep failures gracefully: context retrieval (including
a custom `context_builder`) and persistence log a warning and return
`None`/an empty result, the search tool returns an error string, the
`pre_model_hook` degrades to base-instructions-only, and `ZepStore` keeps
serving KV operations from its backing store. **A Zep failure never crashes
the host agent.**

`ensure_user` / `ensure_thread` (and their `_sync` twins) are the one
exception, by design: they are meant for explicit, out-of-band provisioning
where a genuine failure (auth, network, 5xx) or an `on_created` hook error
should raise loudly and stop startup/onboarding, rather than degrade silently.
An "already exists" conflict is not an error -- it returns `False`.

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
