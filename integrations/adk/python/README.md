# Zep integration for Google ADK

`zep-adk` connects Google ADK agents to Zep v4. It persists conversation
messages, retrieves graph context, and supports ADK's native memory service.

> **Security:** `ZepContextTool` places retrieved context in the model's system
> instruction. Use it only when all stored content is trusted and
> application-authored. Use `ZepGraphSearchTool` or `ZepMemoryService` for
> end-user or third-party content.

## Requirements

- Python 3.11 or later
- `google-adk>=1.19.0,<3`
- `zep-cloud>=4.0.0,<5`

Install the package:

```bash
pip install zep-adk
```

Set `ZEP_API_KEY` before you create the Zep client. Set `GOOGLE_API_KEY` to run
the Gemini example.

## Provision users and threads

Create Zep resources before the first agent turn. Zep generates each UUID.
Store the returned UUIDs in your application database.

```python
import os

from zep_cloud.client import AsyncZep
from zep_adk import create_thread, create_user

zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

user = await create_user(
    zep,
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
)
thread = await create_thread(zep, user_uuid=user.uuid_)

# Save user.uuid_ and thread.uuid_ in your own database.
```

`create_user` returns a v4 `User`; `create_thread` returns a v4 `Thread`.
Use `user.uuid_` as the user's graph UUID. These helpers create a new resource
on each call and do not accept client-chosen IDs.

The turn path does not create resources. Provision each user and thread out of
band. Provisioning errors raise so the application can handle them before the
agent runs.

## Session state

Pass the Zep UUIDs in ADK session state:

| Key | Required | Purpose |
| --- | --- | --- |
| `zep_user_uuid` | Yes | UUID of the Zep user |
| `zep_thread_uuid` | Yes | UUID of the Zep thread |
| `zep_graph_uuid` | No | User graph UUID; defaults to `user.get(zep_user_uuid).uuid_` for graph tools |
| `zep_first_name` | Recommended | Author name for persisted user messages |
| `zep_last_name` | No | Author name for persisted user messages |

ADK `user_id` and `session_id` are fallbacks only when they contain the
corresponding Zep UUID. The integration makes no lookup calls. The graph is
per-user, so it can contain knowledge from several threads.

## Trusted automatic context

`ZepContextTool` persists each user message with
`thread.add_messages(return_context=True)` and adds the returned context to the
model request. Pair it with `create_after_model_callback` to persist the
assistant's final response.

```python
from zep_adk import ZepContextTool, create_after_model_callback

agent = Agent(
    name="memory_agent",
    model="gemini-2.5-flash",
    instruction="You are a helpful assistant.",
    tools=[ZepContextTool(zep_client=zep)],
    after_model_callback=create_after_model_callback(zep_client=zep),
)
```

`ZepContextTool` accepts `ignore_roles`, `context_builder`, and
`context_template`. `ignore_roles` excludes roles from graph ingestion while
messages remain in thread history. A `context_builder` receives one
`ContextInput` value with `zep`, `user_uuid`, `thread_uuid`, `user_message`,
`tool_context`, and `llm_request`.

The builder runs concurrently with message persistence. A builder failure skips
context injection and does not block persistence. A persistence failure does
not block a successful builder result. Return `None` to skip injection.

`context_template` defaults to `DEFAULT_CONTEXT_TEMPLATE`. The template must
contain `{context}`. The integration uses plain string replacement.

```python
from zep_adk import ZepContextTool

tool = ZepContextTool(
    zep_client=zep,
    context_template="Relevant memory:\n{context}",
)
```

## Model-callable graph search

Use `ZepGraphSearchTool` when retrieval must remain an actual model tool call.
Without `graph_uuid`, it resolves `zep_graph_uuid` from session state or gets
the graph from `user.get`. A fixed `graph_uuid` targets a shared graph.

The tool supports `edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, and `auto`. V4 scoped searches use pager `.items` and map
`limit` to `page_size`. The `auto` scope uses `graph.get_context`.

Parameters are exposed to the model by default. Pass a concrete value to pin
and hide that parameter. Pass `None` to hide an optional parameter without
sending it to the SDK.

```python
tool = ZepGraphSearchTool(
    zep_client=zep,
    limit=5,
    reranker="cross_encoder",
    mmr_lambda=None,
    center_node_uuid=None,
    search_filters={"edge_types": ["WORKS_AT"]},
    bfs_origin_node_uuids=["node-uuid"],
)
```

`search_filters` and `bfs_origin_node_uuids` are constructor-only. V4 edge
search does not support `node_labels` or `exclude_node_labels`, although the
shared ADK specification shows `node_labels` in its Python example. The tool
returns an error without searching when a filter is unsupported. V4 observation
search does not support filters. The `auto` context endpoint does not support
`limit`, reranker, or BFS parameters; explicit use of those parameters returns
an error.

## ADK memory service

Register `ZepMemoryService` on the ADK `Runner` to support `load_memory` and
`preload_memory`. It supports the same six scopes. `limit` maps to `page_size`;
`auto` uses `graph.get_context`. A search failure returns an empty result so it
does not interrupt the agent. `add_session_to_memory` is a no-op because the
turn path persists messages as they arrive.

```python
from google.adk.tools import load_memory
from zep_adk import ZepMemoryService

agent = Agent(
    name="memory_agent",
    model="gemini-2.5-flash",
    instruction="Use load_memory to recall relevant facts.",
    tools=[load_memory],
)
runner = Runner(
    agent=agent,
    app_name="my_app",
    session_service=session_service,
    memory_service=ZepMemoryService(zep=zep, scope="edges", limit=10),
)
```

## Per-user setup

Run custom ontology and instruction setup after `create_user` returns. Python
does not provide an `on_created` hook.

```python
from zep_cloud import CustomInstruction, UserInstruction

await zep.graph.set_instructions(
    user.uuid_,
    inherited=False,
    instructions=[
        CustomInstruction(
            name="purchase_intent",
            text="Extract product preferences and purchase intent.",
        )
    ],
)
await zep.user.set_summary_instructions(
    user.uuid_,
    inherited=False,
    instructions=[
        UserInstruction(
            name="work_focus",
            text="Focus on the user's role, team, and active projects.",
        )
    ],
)
```

See `examples/basic_agent.py` for a complete agent example and `SETUP.md` for
installation and test commands.

## Upgrading from v3

- Replace `ensure_user` and `ensure_thread` with `create_user` and
  `create_thread`. Read `user.uuid_` and `thread.uuid_` from the responses.
- Replace `zep_user_id` and `zep_thread_id` with `zep_user_uuid`,
  `zep_thread_uuid`, and optional `zep_graph_uuid`.
- Remove `on_created` and `UserSetupHook`. Run one-time setup after
  `create_user` returns.
- Update `ContextInput` access from `user_id` and `thread_id` to `user_uuid`
  and `thread_uuid`. Builders take one `ContextInput` argument.
- Replace generic `graph.search` calls with the v4 scoped search methods.
  Use `page_size` and pager `.items`.
- Pin `zep-cloud` to `>=4.0.0,<5`.

## Development

Run the package checks from this directory:

```bash
make install
make lint
make type-check
make test
make build
```

The live tests use `ZEP_API_KEY`. The Gemini agent test also requires
`GOOGLE_API_KEY`.
