# Zep Microsoft Agent Framework Integration

This package adds long-term memory to Microsoft Agent Framework agents. It uses
the Zep V4 SDK and the Zep Context Graph.

> **Security:** `ZepContextProvider` adds retrieved context to model
> instructions. Use it only when all stored content is trusted and
> application-authored. Use `create_zep_search_tool` for user-provided or
> third-party content.

## Install

```bash
pip install zep-ms-agent-framework
```

The package requires Python 3.11 or later. The example uses the OpenAI model
provider:

```bash
pip install zep-ms-agent-framework agent-framework-openai
```

## Quick start

Create the Zep user and thread before the agent runs. Zep creates their UUIDs.
Store these UUIDs in your application database.

```python
import asyncio

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from zep_cloud.client import AsyncZep
from zep_ms_agent_framework import (
    ZepContextProvider,
    create_thread,
    create_user,
)

zep = AsyncZep(api_key="your-zep-api-key")


async def main() -> None:
    user = await create_user(
        zep,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )
    thread = await create_thread(zep, user_uuid=user.uuid_)

    agent = Agent(
        OpenAIChatClient(model="gpt-5-mini"),
        instructions="You are a helpful assistant with long-term memory.",
        context_providers=[
            ZepContextProvider(
                zep_client=zep,
                user_uuid=user.uuid_,
                thread_uuid=thread.uuid_,
                graph_uuid=user.uuid_,
                user_message_name="Jane Smith",
            )
        ],
    )

    result = await agent.run("Hi, I am a data scientist in Portland.")
    print(result.text)


asyncio.run(main())
```

Use one provider for each user and conversation. A new thread for the same user
can recall facts from earlier threads.

## Lifecycle

`ZepContextProvider` uses the Agent Framework `before_run` and `after_run`
hooks:

1. `before_run` reads the latest user message.
2. It persists the message to the configured thread and gets Zep context in the
   same call. If you set `context_builder`, it persists the message and runs
   the builder concurrently.
3. It adds the context to the agent instructions through `source_id`.
4. `after_run` persists the assistant response to the same thread.

The provider does not create or resolve users or threads on the turn path. Call
`create_user` and `create_thread` before you construct the provider.
The provider makes no lookup calls on the turn path.

Zep errors do not interrupt an agent run. If the user-message write fails, the
provider logs the failure and does not write an assistant-only message after
the run. If context retrieval fails after persistence succeeds, the provider
continues without injected context.

## Provisioning

`create_user` and `create_thread` return resources with server-generated UUIDs.
The helpers do not accept caller-selected IDs and do not treat conflicts as
success.

```python
from zep_ms_agent_framework import create_thread, create_user


async def setup_user(zep_client, user_uuid: str) -> None:
    # Configure per-user ontology or instructions.
    ...


user = await create_user(
    zep,
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,
)
thread = await create_thread(zep, user_uuid=user.uuid_)
```

`on_created` receives the Zep client and the new user UUID. It runs after user
creation. Errors from Zep or the hook propagate to the caller. Run provisioning
outside the agent turn path.

## Upgrading from V3

- Replace `user_id` and `thread_id` with the server-generated `user_uuid` and
  `thread_uuid` values from `create_user` and `create_thread`.
- Move `first_name`, `last_name`, and `email` from `ZepContextProvider` to
  `create_user`.
- Replace `ensure_user` and `ensure_thread` with explicit create calls. The
  provider no longer provisions resources during an agent turn.
- Move `on_user_created` to the `on_created` argument of `create_user`.
- Pass `graph_uuid` when you create a graph-search tool. Use V4 scope-specific
  graph search methods; the `auto` scope uses `graph.get_context`.

## Custom context building

Use `context_builder` to replace the context returned by
`thread.add_messages(return_context=True)`. The asynchronous builder receives a
`ContextInput` with the Zep client, user UUID, thread UUID, graph UUID, user
message, and Agent Framework session context.

```python
from zep_ms_agent_framework import ContextInput, ZepContextProvider


async def build_context(context: ContextInput) -> str | None:
    page = await context.zep.graph.search_edges(
        context.graph_uuid or context.user_uuid,
        query=context.user_message,
        page_size=5,
    )
    return "\n".join(edge.fact for edge in page.items if edge.fact)


provider = ZepContextProvider(
    zep_client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.uuid_,
    context_builder=build_context,
)
```

The provider runs persistence and the builder concurrently. A builder failure
does not stop persistence. A persistence failure does not prevent a successful
builder result from being injected. The provider logs either failure and does
not raise it into the agent run.

## Context template

Set `context_template` to wrap retrieved context. The template must contain a
literal `{context}` token. The provider uses string replacement, so braces and
percent signs in the context are safe.

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    context_template="Relevant memory:\n{context}",
)
```

The default is `DEFAULT_CONTEXT_TEMPLATE`, which wraps context in a
`<ZEP_CONTEXT>` block.

## On-demand graph search

Use `create_zep_search_tool` to create an `agent_framework.FunctionTool`, or set
`expose_search_tool=True` to add the tool to every agent run. Search requires a
`graph_uuid`.

```python
from zep_ms_agent_framework import create_zep_search_tool

search_tool = create_zep_search_tool(
    zep_client=zep,
    graph_uuid=user.uuid_,
    search_filters={"node_labels": ["Person"]},
    bfs_origin_node_uuids=["person-node-uuid"],
)

provider = ZepContextProvider(
    zep_client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.uuid_,
    expose_search_tool=True,
    search_pinned_params={"scope": "nodes", "limit": 5},
    search_hidden_params={"mmr_lambda"},
)
```

The tool supports `edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, and `auto`. The `auto` scope uses `graph.get_context`.
Other scopes use their matching V4 search methods and return paged results.

The tool exposes `scope`, `reranker`, `limit`, `mmr_lambda`, and
`center_node_uuid` to the model by default. `search_pinned_params` fixes a
parameter and hides it from the model. `search_hidden_params` hides a parameter
without setting a value. The default result limit is 10 and the tool clamps it
to the range 1 through 50. `search_filters` and `bfs_origin_node_uuids` are
constructor-only. A search failure returns an error string to the model.

The default scope is `edges`. The default reranker is `rrf`. The other
rerankers are `mmr`, `node_distance`, `episode_mentions`, and `cross_encoder`.
The `limit` parameter accepts values from 1 through 50.

The V4 SDK `graph.get_context` request does not accept a reranker, page size,
MMR lambda, center-node UUID, or BFS origin UUIDs. The `auto` scope therefore
sends the query and supported filters only. The SDK uses
`EdgeSearchFilters`, `NodeSearchFilters`, `EpisodeSearchFilters`, and
`ThreadSummarySearchFilters` for their matching scopes. It uses
`SearchFilters` for `auto`. `ObservationSearchFilters` is a dictionary type in
the SDK, so observation filters use a dictionary.

`create_zep_search_tool` accepts these constructor parameters:

| Parameter | Required | Default | Purpose |
| --- | --- | --- | --- |
| `zep_client` | Yes | — | The initialized `AsyncZep` client. |
| `graph_uuid` | Yes | — | UUID of the graph to search. |
| `search_pinned_params` | No | `None` | Parameters to fix and hide from the model. |
| `search_hidden_params` | No | `None` | Parameters to hide without setting a value. |
| `search_filters` | No | `None` | Filters to apply to the selected scope. |
| `bfs_origin_node_uuids` | No | `None` | BFS origin node UUIDs for supported search scopes. |
| `name` | No | `"zep_search"` | Function tool name. |
| `description` | No | Search description | Function tool description. |

## Configuration

| Parameter | Required | Default | Purpose |
| --- | --- | --- | --- |
| `zep_client` | Yes | — | The initialized `AsyncZep` client. The caller owns its lifecycle. |
| `user_uuid` | Yes | — | UUID of the Zep user. |
| `thread_uuid` | Yes | — | UUID of the Zep thread. |
| `graph_uuid` | No | `None` | Graph to search. Required when `expose_search_tool=True`. |
| `user_message_name` | No | `None` | Display name on persisted user messages. Set it to the user's full name when available. |
| `assistant_message_name` | No | `"Assistant"` | Display name on persisted assistant messages. |
| `source_id` | No | `"zep"` | Attribution ID for injected context and tools. |
| `ignore_roles` | No | `None` | Roles to exclude from graph ingestion. Messages remain in thread history. |
| `context_builder` | No | `None` | Asynchronous context retrieval callable. |
| `context_template` | No | `DEFAULT_CONTEXT_TEMPLATE` | Template for injected context. |
| `expose_search_tool` | No | `False` | Register the graph-search tool on every run. |
| `search_pinned_params` | No | `None` | Search parameters to fix and hide from the model. |
| `search_hidden_params` | No | `None` | Search parameters to hide without setting them. |
| `search_filters` | No | `None` | Constructor-only graph-search filters. |
| `bfs_origin_node_uuids` | No | `None` | Constructor-only node UUIDs for breadth-first search. |

The Agent Framework session does not provide an identity field for the
provider. Bind the user and thread UUIDs when you construct the provider.

## Example and tests

See [`SETUP.md`](SETUP.md) for environment setup and test commands. The
cross-thread recall example is in [`examples/basic_agent.py`](examples/basic_agent.py).
