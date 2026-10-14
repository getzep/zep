# Zep memory for AG2

This package adds Zep memory tools and memory managers to AG2 agents. It uses
the Zep V4 SDK.

## Install

```bash
pip install zep-ag2
```

Set `ZEP_API_KEY` before you run an example. Examples that call an LLM also
require `OPENAI_API_KEY`.

## Create a user and thread

Zep creates UUIDs for users, threads, and graphs. Create the user and thread
before the agent's first turn. Store the returned UUIDs in your application.

```python
from zep_cloud.client import AsyncZep
from zep_ag2 import create_thread, create_user

zep = AsyncZep(api_key="...")
user = await create_user(zep, first_name="Ada", email="ada@example.com")
thread = await create_thread(zep, user_uuid=user.uuid_)
user_uuid = user.uuid_
graph_uuid = user.uuid_
thread_uuid = thread.uuid_
```

The personal graph UUID is the user's UUID. `ZepMemoryManager` can read the
graph UUID from the user when you omit `graph_uuid`.

## Use the memory manager

`ZepMemoryManager` stores thread messages and retrieves context. Pass
`context_builder` to replace the default context retrieval. Pass
`context_template` to change the text that wraps retrieved context.

```python
from zep_ag2 import ZepMemoryManager

memory = ZepMemoryManager(
    zep,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    graph_uuid=graph_uuid,
)
context = await memory.process_user_message("My name is Ada.")
```

`attach_to_agent(agent)` registers hooks that persist received and sent
messages. The hooks also refresh the agent's system message with context.
The hooks catch errors so a Zep failure does not stop the agent's turn.
Attach one manager to one agent per thread. Two attached agents on one thread
can persist the same message twice with different roles.

Use system-message injection only with trusted, application-authored context.
Use search tools for context that users or external sources can change.

## Use the search tools

The search tool factories bind to a `graph_uuid`. Use `user.uuid_` for a
personal graph or a shared Context Graph UUID for shared knowledge.

```python
from zep_ag2 import create_search_graph_tool

search = create_search_graph_tool(
    zep,
    graph_uuid,
    pinned_params={"scope": "edges"},
    hidden_params={"reranker"},
)
```

The model can select `scope`, `reranker`, `limit`, `mmr_lambda`, and
`center_node_uuid` unless you pin or hide them. Supported scopes are
`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, and `auto`.
The `auto` scope uses `graph.get_context`. The `filters` and
`bfs_origin_node_uuids` arguments are constructor-only.
The installed V4 SDK does not expose BFS origins, reranker, page size, MMR
lambda, or center-node UUID on `GraphContextRequest`. The `auto` scope therefore
sends the query and supported filters only.

Use `create_add_memory_tool` to write to a thread or, when no `thread_uuid` is
set, add an episode to the graph. Use `create_add_graph_data_tool` to add
episodes to a graph. The package truncates thread messages at 4,000 characters
and graph data at 9,900 characters.

`ZepGraphMemoryManager` provides programmatic `search`, `add_data`, and
`enrich_system_message` methods for a graph. Its search scopes are `edges`,
`nodes`, and `episodes`.

## API

- `ZepMemoryManager(client, user_uuid, thread_uuid=None, *, graph_uuid=None, context_builder=None, context_template=DEFAULT_CONTEXT_TEMPLATE)`
- `ZepMemoryManager.attach_to_agent(agent)`
- `ZepMemoryManager.process_user_message(user_message, *, agent=None)`
- `ZepMemoryManager.resolve_graph_uuid()`
- `ZepMemoryManager.get_memory_context(query=None, limit=5)`
- `ZepMemoryManager.enrich_system_message(agent, query=None, limit=5)`
- `ZepMemoryManager.get_memory_context_sync(query=None, limit=5)` and
  `enrich_system_message_sync(agent, query=None, limit=5)`
- `ZepMemoryManager.add_messages(messages)` and `get_session_facts()`
- `ZepGraphMemoryManager(client, graph_uuid)` with `search`, `add_data`,
  `enrich_system_message`, `search_sync`, and `add_data_sync`
- `create_user(client, *, first_name=None, last_name=None, email=None, on_created=None)`
- `create_thread(client, *, user_uuid)`
- `create_search_memory_tool` and `create_search_graph_tool`
- `create_add_memory_tool` and `create_add_graph_data_tool`
- `register_all_tools(agent, executor, client, graph_uuid, thread_uuid=None, memory_graph_uuid=None)`

`ContextInput` gives a custom context builder the client, `user_uuid`,
`thread_uuid`, `user_message`, and optional AG2 agent. Builder and persistence
errors are isolated on the turn path. `create_user` can call `on_created`
with the server-generated user UUID after it creates the user.

The search SDK methods use typed filters for each scope. The tool accepts a
filter value through its constructor-only `filters` argument.

## Examples

- [`ag2_basic.py`](examples/ag2_basic.py) creates a user and thread, then attaches memory.
- [`ag2_graph.py`](examples/ag2_graph.py) uses a shared Context Graph.
- [`ag2_tools_search.py`](examples/ag2_tools_search.py) registers a search tool.
- [`ag2_tools_full.py`](examples/ag2_tools_full.py) registers all tools.
- [`manual_test.py`](examples/manual_test.py) runs a live Zep memory check.

## Development

```bash
make install
make pre-commit
make ci
```

The package requires Python 3.11+, `ag2>=0.9.0,<1`, and
`zep-cloud>=4.0.0,<5`.

## Links

- [Zep documentation](https://help.getzep.com)
- [AG2 documentation](https://docs.ag2.ai)
- [License](../../../LICENSE)
