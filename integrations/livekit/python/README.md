# Zep memory for LiveKit

This package adds Zep memory to LiveKit agents. It supports thread memory,
Context Graph memory, and a graph search tool.

The package uses the Zep V4 Python SDK. It requires Python 3.11 or later and
`zep-cloud>=4.0.0,<5`.

## Create a user and thread

Create Zep resources before you start a voice session. Zep assigns their UUIDs.
Save these UUIDs in your application database.

```python
from zep_cloud.client import AsyncZep
from zep_livekit import create_thread, create_user

zep = AsyncZep(api_key="...")
user = await create_user(
    zep,
    first_name="Jane",
    email="jane@example.com",
)
thread = await create_thread(zep, user_uuid=user.uuid_)

print(user.uuid_, thread.uuid_)
```

`create_user` returns the created `User`. It accepts an optional `on_created`
async hook. The hook receives the Zep client and the new user UUID. Provisioning
and hook errors propagate to the caller.

`create_thread` returns the created `Thread`. It takes the user UUID. Resource
creation does not accept caller-selected UUIDs.

## Use thread memory

Pass the saved UUIDs to `ZepUserAgent`:

```python
from zep_livekit import ZepUserAgent

agent = ZepUserAgent(
    zep_client=zep,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    instructions="You are a helpful assistant.",
)
```

The agent stores each user message with `thread.add_messages` and requests
context in the same call with `return_context=True`. A LiveKit session event
stores assistant messages separately. The agent truncates message content to
4,000 characters, below Zep's 4,096-character limit.

You can provide an async `context_builder`. It receives a `ContextInput` with
`zep`, `user_uuid`, `thread_uuid`, `user_message`, and `session`. When you set a
builder, persistence and context building run independently. An error in
either operation does not stop the voice turn.

You can set `user_message_name`, `assistant_message_name`, and
`context_template`. The template must contain the literal `{context}` text.
Zep LiveKit replaces this text without using Python format rules.

## Use Context Graph memory

Pass a graph UUID to `ZepGraphAgent`:

```python
import os

from zep_livekit import ZepGraphAgent

agent = ZepGraphAgent(
    zep_client=zep,
    graph_uuid=graph_uuid,
    user_name="Jane",
    max_characters=6000,
    template_uuid=os.getenv("ZEP_TEMPLATE_UUID") or None,
    instructions="You are a helpful assistant.",
)
```

The agent stores user and assistant messages as graph episodes with
`graph.episode.add`. It retrieves context with `graph.get_context`. It applies
`search_filters` and `max_characters` to this request. The agent truncates
episode data to 9,900 characters, below Zep's 10,000-character limit.
Set `template_uuid` to use a saved Zep context template.

An optional async `context_builder` receives a `GraphContextInput` with
`zep`, `graph_uuid`, `user_message`, and `session`. Its errors do not stop the
voice turn. Both agents use `DEFAULT_CONTEXT_TEMPLATE` by default.

## Add a graph search tool

The tool searches a graph when the model requests it:

```python
from zep_livekit import create_graph_search_tool

search_tool = create_graph_search_tool(
    zep,
    graph_uuid=graph_uuid,
    search_filters={"node_labels": ["Person"]},
    pinned_params={"scope": "edges", "limit": 10},
)
```

The tool supports `edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, and `auto`. The first five scopes use their V4
scope-specific search methods. The `auto` scope uses `graph.get_context`.
Search filters and BFS origin UUIDs are set when you create the tool. The
`pinned_params` option fixes and hides parameters. The `hidden_params` option
hides parameters and uses Zep defaults. The tool returns errors as strings so
they do not escape into a voice session.

The installed V4 SDK does not expose reranker, page-size, MMR, center-node, or
BFS fields on `graph.get_context`. The `auto` scope therefore uses the query
and supported filters only.

## Examples and tests

The [`examples/`](examples) directory contains thread and graph voice agents.
Set `ZEP_API_KEY`, `OPENAI_API_KEY`, and the LiveKit server variables to run
them. Set `ZEP_USER_UUID` and `ZEP_THREAD_UUID` for the thread example. Set
`ZEP_GRAPH_UUID` for the graph example.

Run unit tests and quality checks with:

```bash
make ci
```

Run the live memory test with `ZEP_API_KEY`. It does not start a LiveKit room
or call an LLM:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```
