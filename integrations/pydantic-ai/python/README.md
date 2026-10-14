# Zep integration for Pydantic AI

This package adds Zep memory to Pydantic AI agents. It stores user and assistant messages, adds trusted context to a run, and provides a model-callable graph-search tool.

> **Context security**
>
> The history processor adds retrieved context as a system message. Use this only when all stored content is trusted and application-authored. For user-supplied or document content, retrieve it through an actual tool call.

## Install

```bash
pip install zep-pydantic-ai
```

The package supports Python 3.11 and later, Pydantic AI 1.107 or later in the 1.x series, and Zep Cloud V4. Set `ZEP_API_KEY` before you create the Zep client.

## Quick start

Create the user and thread before the agent run. The helpers return V4 SDK models with server-generated UUIDs.

```python
from pydantic_ai import Agent
from zep_cloud.client import AsyncZep
from zep_pydantic_ai import (
    ZepDeps,
    create_thread,
    create_user,
    create_zep_search_tool,
    zep_capabilities,
)

zep = AsyncZep()
user = await create_user(zep, first_name="Jane", last_name="Smith")
thread = await create_thread(zep, user_uuid=user.uuid_)

deps = ZepDeps(
    client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.uuid_,
)
agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=ZepDeps,
    capabilities=zep_capabilities(deps),
    tools=[create_zep_search_tool()],
)

result = await agent.run("What do you remember about me?", deps=deps)
print(result.output)
```

`zep_capabilities(deps)` persists the user turn and adds context before each model request. Its `after_run` hook persists assistant text. The history processor deduplicates user persistence for tool-calling runs. Use `persist_run(deps, result.new_messages())` when you need to control assistant persistence.

`ZepDeps` also accepts `first_name`, `last_name`, `user_name`, and `assistant_name` for persisted message names, `ignore_roles` to exclude messages from graph ingestion, and `context_template` to wrap trusted context. The processor truncates long messages to the Zep limit and logs Zep failures without raising into the agent run.

The integration uses the UUIDs in `ZepDeps` on the run path. It does not create or look up resources during a run. Store the returned UUIDs in your application database.

## Search

`create_zep_search_tool()` exposes the scopes `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, and `auto`. The `auto` scope calls `graph.get_context`; the other scopes call the V4 scope-specific search methods.

The model can choose `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid`. Use `pinned_params` to set a fixed value and hide a parameter. Use `hidden_params` to hide a parameter and let the server apply its default. The tool clamps `limit` to the supported range and returns search errors as text.

```python
tool = create_zep_search_tool(
    pinned_params={"scope": "nodes", "limit": 5},
    filters={"node_labels": ["Person"]},
    bfs_origin_node_uuids=["node-uuid"],
)
```

The `filters` and `bfs_origin_node_uuids` arguments are constructor-only. The SDK does not accept paging, reranker, MMR, center-node, or BFS options on `graph.get_context`. The `auto` scope sends the query and supported filters.

## Custom context

Set `context_builder` to provide trusted context from a custom V4 query. The builder receives a frozen `ContextInput` with the Zep client, UUIDs, user message, and Pydantic AI run context. A builder failure skips context injection and does not prevent message persistence.

Set `context_template` to change the wrapper for the retrieved context. The template must contain `{context}`.

## Create and delete resources

`create_user` accepts identity fields and an optional `on_created` hook. `create_thread` accepts the server-generated `user_uuid`. Each helper returns the SDK model. Use `user.uuid_` and `thread.uuid_` for `ZepDeps`.

Delete test or temporary resources with the V4 SDK:

```python
await zep.thread.delete(thread.uuid_)
await zep.user.delete(user_uuid=user.uuid_)
```

## Development

```bash
make install
make format
make lint
make type-check
make test
```

See [SETUP.md](SETUP.md) for API-key setup and the live integration test. See [CHANGELOG.md](CHANGELOG.md) for upgrade notes.
