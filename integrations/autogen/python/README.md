# Zep integration for AutoGen

This package connects AutoGen agents to Zep v4. It provides user and shared
graph memory, search and add tools, and server-generated UUID provisioning.

Use memory classes only for fully trusted, application-authored content. Use
function tools when graph data can contain end-user or third-party content.

## Install

```bash
pip install zep-autogen
```

The package requires Python 3.11 or later, AutoGen 0.7 or later, and
`zep-cloud>=4.0.0,<5`.

Set `ZEP_API_KEY` in the environment before you create the client:

```python
import os
from zep_cloud.client import AsyncZep

zep_client = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
```

## User memory

Zep v4 creates the user and thread UUIDs. Store these UUIDs in your database.
The integration does not call lookup methods.

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_autogen import ZepUserMemory, create_thread, create_user

user = await create_user(zep_client, first_name="Alice", email="alice@example.com")
user_uuid = str(user.uuid_)
thread = await create_thread(zep_client, user_uuid=user_uuid)
thread_uuid = str(thread.uuid_)

memory = ZepUserMemory(
    client=zep_client,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
)
agent = AssistantAgent(
    name="Assistant",
    model_client=OpenAIChatCompletionClient(model="gpt-4o-mini"),
    memory=[memory],
)
```

AutoGen calls `update_context()` before a model call. The application must call
`add()` to persist each user and assistant message:

```python
await memory.add(
    MemoryContent(
        content="My name is Alice and I like hiking.",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "user", "name": "Alice"},
    )
)
response = await agent.run(task="My name is Alice and I like hiking.")
await memory.add(
    MemoryContent(
        content=str(response.messages[-1].content),
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "assistant"},
    )
)
```

If `thread_uuid` is omitted, the first message added to the memory creates a
thread. A thread creation error is logged and swallowed. Call `create_thread()`
before the turn when the application must handle a creation error.

`create_user()` accepts an optional asynchronous `on_created` hook. It calls
the hook after a successful create and passes the client and server-created
user UUID. An error from the hook propagates to the caller.

### Context retrieval

The default path calls `thread.get_context()` and can append up to 10 recent
thread messages. Set `context_template_uuid` to select a Zep context template.
Use `context_builder` to replace the default retrieval:

```python
from zep_autogen import ZepUserMemory
from zep_autogen.memory import ContextInput


async def build_context(ctx: ContextInput) -> str | None:
    page = await ctx.zep.graph.search_edges(
        ctx.graph_uuid,
        query=ctx.user_message,
        page_size=5,
    )
    return "\n".join(edge.fact for edge in page.items if edge.fact) or None


memory = ZepUserMemory(
    client=zep_client,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    context_builder=build_context,
    context_template="Relevant background:\n{context}",
)
```

`ContextInput` has `zep`, `user_uuid`, `thread_uuid`, `graph_uuid`,
`user_message`, and `model_context`. The builder is asynchronous and retrieval
only. AutoGen controls persistence in a separate `add()` call. If the builder
or a Zep context request fails, the error is logged and the agent turn continues
without injected context. The template uses string replacement and must
contain `{context}`.

`ZepUserMemory` accepts `graph_uuid` when data must go to a graph other than
the user's graph. If it is omitted, the user UUID identifies the user's graph.
`Memory.query()` accepts `scope` and `limit`; supported scopes are
`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, and `auto`.

## Shared Context Graph memory

Create a graph and use its returned UUID. The context builder reads the two
most recent graph episodes, then calls `graph.get_context()` with their text.
`max_characters` limits the injected context block.

```python
from autogen_core.memory import MemoryContent, MemoryMimeType
from zep_autogen import ZepGraphMemory

graph = await zep_client.graph.create(name="Programming Knowledge")
graph_uuid = str(graph.uuid_)
memory = ZepGraphMemory(
    client=zep_client,
    graph_uuid=graph_uuid,
    max_characters=4000,
)

await memory.add(
    MemoryContent(
        content="Python is used for data science.",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "data"},
    )
)
```

Pass `SearchFilters` to `search_filters` to limit retrieval. `ZepGraphMemory`
supports the same six query scopes as user memory. Zep processes graph
episodes asynchronously, so wait for indexing before you query new data.

## Function tools

Bind each tool to exactly one `graph_uuid` or `user_uuid`. The search tool
exposes `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid` to
the model by default. Use `pinned_params` to fix and hide a parameter, or
`hidden_params` to omit it from the tool schema and use the server default.
The legacy `scope` and `limit` arguments also pin the corresponding value.
`search_filters` and `bfs_origin_node_uuids` are constructor-only.

```python
from zep_autogen import create_add_graph_data_tool, create_search_graph_tool

search_tool = create_search_graph_tool(
    zep_client,
    user_uuid=user_uuid,
    pinned_params={"scope": "nodes", "limit": 5},
)
add_tool = create_add_graph_data_tool(zep_client, user_uuid=user_uuid)
```

Search scopes are:

| Scope | Result |
|---|---|
| `edges` | Facts and relationships |
| `nodes` | Entities and summaries |
| `episodes` | Source text, including messages and JSON |
| `observations` | Derived observations |
| `thread_summaries` | Thread summaries |
| `auto` | Context selected by Zep |

The `auto` scope calls `graph.get_context()` with the query and supported
filters. The v4 `GraphContextRequest` does not have fields for reranking, page
size, MMR lambda, center-node UUID, or BFS origins. These controls apply only
to the scope-specific search methods.

The add tool accepts `data` and `data_type` (`text`, `json`, or `message`).
Zep errors from memory and tools are logged; they do not fail an agent turn.
The integration truncates thread messages to 4,000 characters and graph data
to 9,900 characters before it sends the request.

## Examples and tests

See [`examples/`](examples) for user memory, shared graph memory, search-only
tools, and search-plus-add tools.

```bash
make install
make lint
make format
make type-check
make test
```

Run the live test with `ZEP_API_KEY` and `OPENAI_API_KEY` set:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```
