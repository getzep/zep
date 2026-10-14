# Zep CrewAI integration

`zep-crewai` connects CrewAI agents to Zep users, threads, and Context Graphs. It supports CrewAI 1.x and the synchronous `zep-cloud` V4 client.

## Install

```bash
pip install zep-crewai
```

Python 3.11+ is required. The package installs `zep-cloud>=4.0.0,<5`.

## Create a user and thread

Create resources once and store their returned UUIDs in your application:

```python
from zep_cloud.client import Zep
from zep_crewai import ZepUserStorage, create_search_tool, ensure_thread, ensure_user

zep = Zep(api_key="...")
user, _ = ensure_user(zep, first_name="Alice", email="alice@example.com")
thread, _ = ensure_thread(zep, user_uuid=user.uuid_)

storage = ZepUserStorage(
    client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
)
storage.save(
    "Alice prefers morning meetings",
    metadata={"type": "text"},
)
storage.save(
    "Good morning, Alice.",
    metadata={"type": "message", "role": "assistant"},
)

tool = create_search_tool(zep, graph_uuid=user.uuid_)
```

`graph_uuid` is optional for `ZepUserStorage`. When omitted, it reads the user once and caches the returned graph UUID. `ensure_user` and `ensure_thread` create server-identified resources and return `(resource, created)`. Each successful call creates a new resource, so call the helpers once during onboarding. `ensure_user` accepts an optional `on_created(client, user)` hook.

## Shared graph storage

Create a graph and keep its `uuid_`:

```python
from zep_crewai import ZepGraphStorage, create_search_tool

graph = zep.graph.create(name="Company knowledge")
storage = ZepGraphStorage(client=zep, graph_uuid=graph.uuid_)
storage.save("Project Atlas uses Python", metadata={"type": "text"})
search_tool = create_search_tool(zep, graph_uuid=graph.uuid_)
```

`ZepUserStorage`, `ZepGraphStorage`, and the compatibility adapter `ZepStorage` expose `save`, `search`, and `reset`. CrewAI 1.x no longer supports the `ExternalMemory(storage=...)` extension point. Call storage from your application, or add Zep tools to an agent.

## Search tool

The tool supports `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, and `auto`. It uses the matching V4 search method; `auto` calls `graph.get_context`. Every scoped search reads its first page from `.items`.

The model can control `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid`. Use `pinned_params` to fix and hide a parameter, or `hidden_params` to omit it and use the server default. `search_filters` and `bfs_origin_node_uuids` are constructor-only:

```python
from zep_cloud import SearchFilters

tool = create_search_tool(
    zep,
    graph_uuid=user.uuid_,
    pinned_params={"scope": "nodes", "limit": 5},
    hidden_params={"reranker"},
    search_filters=SearchFilters(node_labels=["Project"]),
    bfs_origin_node_uuids=["node-uuid"],
)
```

The V4 SDK accepts different filter fields for each search scope. The tool converts supported `SearchFilters` fields to the filter type for the selected scope. If a field is not supported, the tool returns an error instead of dropping it.

The `auto` scope sends the query and supported filters to `graph.get_context`. That SDK method does not accept reranker, page size, BFS origins, MMR, or center-node parameters. The tool does not send those parameters for `auto`.

The SDK does not support `node_labels` for edge search. Set `scope="nodes"` when you use a node-label filter with the tool.

Search and add-data tool failures return error text instead of raising into the crew. Storage `save` failures are logged and do not raise.

## Context and templates

`ZepUserStorage.get_context()` reads the thread context. `search()` uses `graph.get_context` by default. A synchronous `context_builder(ContextInput)` can replace that search behavior. The input includes the client, user, thread, graph UUIDs, and query. Builder failures are logged and return no results.

`context_template` wraps the context with a literal `{context}` replacement. `template_uuid` selects a Zep context template. `max_characters` limits graph context output.

## Data limits

The integration truncates thread messages to 4,000 characters and graph episode data to 9,900 characters. Search queries are truncated to 400 characters. The search tool clamps `limit` to 1–50.

## Upgrade from V3

Install the V4 SDK, replace `user_id`, `thread_id`, and `graph_id` with UUID values, and use the UUIDs returned by `ensure_user`, `ensure_thread`, and `graph.create`. V4 graph data uses `graph.episode.add`; search uses scope-specific methods. `ExternalMemory(storage=...)` is not available in CrewAI 1.x; use explicit storage calls and tools.

The examples in [`examples/`](examples) show user storage, shared graph storage, context retrieval, and tools. See [SETUP.md](SETUP.md) for local setup and tests.
