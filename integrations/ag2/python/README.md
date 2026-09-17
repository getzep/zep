# zep-ag2

Zep memory integration for [AG2](https://ag2.ai)

## Installation

```bash
pip install zep-ag2
```

## Identifiers in Zep v4

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
`user_id` or a `thread_id` is a name, not an address. The public API of this
package takes `user_uuid`, `thread_uuid`, and `graph_uuid`.

Create the user and the thread one time with `create_user` and `create_thread`,
read the UUIDs from the responses, and store them in your own database. The
package does not resolve a name at run time.

```python
from zep_ag2 import create_thread, create_user

user = await create_user(zep, first_name="Jane", email="jane@example.com")
thread = await create_thread(zep, user_uuid=user.uuid_)

# Store these three values in your application database.
user.uuid_, user.graph_uuid, thread.uuid_
```

## Quick Start

### System Message Injection

```python
import asyncio
import os
from autogen import AssistantAgent, UserProxyAgent, LLMConfig
from zep_cloud.client import AsyncZep
from zep_ag2 import ZepMemoryManager, create_thread, create_user, register_all_tools


async def main():
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    # Create these one time and store the UUIDs in your own database.
    user = await create_user(zep, first_name="Jane", email="jane@example.com")
    thread = await create_thread(zep, user_uuid=user.uuid_)
    user_uuid, graph_uuid, thread_uuid = user.uuid_, user.graph_uuid, thread.uuid_

    llm_config = LLMConfig({"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]})

    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message="You are a helpful assistant with long-term memory.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Enrich agent with memory context. The UUIDs come from your database.
    memory_mgr = ZepMemoryManager(zep, user_uuid, thread_uuid, graph_uuid=graph_uuid)
    await memory_mgr.enrich_system_message(assistant, query="conversation topic")

    # Register memory tools (sync — AG2 calls them automatically)
    register_all_tools(assistant, user_proxy, zep, graph_uuid, thread_uuid)

    result = user_proxy.initiate_chat(assistant, message="What do you remember about me?")


asyncio.run(main())
```

### Tool-Based Memory Access

```python
from zep_ag2 import create_search_graph_tool, create_add_graph_data_tool

# Create tools bound to the graph of one user
search_tool = create_search_graph_tool(zep, graph_uuid)
add_tool = create_add_graph_data_tool(zep, graph_uuid)

# Register with AG2's decorator pattern
assistant.register_for_llm(description="Search knowledge graph")(search_tool)
user_proxy.register_for_execution()(search_tool)

assistant.register_for_llm(description="Add to knowledge graph")(add_tool)
user_proxy.register_for_execution()(add_tool)
```

### Automatic Memory Loop (`attach_to_agent`)

AG2 has no native memory interface -- `ConversableAgent.register_hook` is the framework's
closest thing to a per-turn seam. `attach_to_agent` wires a fully automatic inject+persist
loop onto that seam, so you don't have to call `enrich_system_message`/`add_messages`
yourself on every turn:

```python
from zep_ag2 import ZepMemoryManager

manager = ZepMemoryManager(zep, user_uuid, thread_uuid, graph_uuid=graph_uuid)
manager.attach_to_agent(assistant)

# Every message the agent receives is persisted and used to refresh its system
# message; every reply the agent sends is persisted automatically too.
user_proxy.initiate_chat(assistant, message="My name is Alice.")
```

See "The automatic memory loop, precisely" below for the exact hook wiring and what is (and
isn't) covered.

## Features

- **Tool-based memory access** — Register Zep search/add as AG2 tools via `@register_for_llm`
- **System message injection** — Automatically enrich agent context with relevant memories
- **Automatic memory loop** — `attach_to_agent` wires inject-on-receive and persist-on-send onto AG2's `register_hook` seam
- **Knowledge graph** — Access Zep's knowledge graph from AG2 agents
- **Conversation memory** — Store and retrieve thread-based conversation history
- **Out-of-band provisioning** — `create_user`/`create_thread` for explicit, fail-loud setup before the first turn
- **Sync tool execution** — Tools run synchronously via a single shared background event loop, compatible with AG2's execution model on Python 3.11–3.13

## The automatic memory loop, precisely

`ZepMemoryManager.attach_to_agent(agent)` registers two hooks on `ConversableAgent`:

- **`process_last_received_message`** fires for every message the agent receives. It
  persists the message and retrieves context (via `process_user_message` internally,
  bridged through the package's sync loop), then replaces the agent's system message with
  its original text plus the freshly-rendered `context_template`. The hook always returns
  the message content **unmodified** -- this is a side channel, not a message transform.
- **`process_message_before_send`** fires for every message the agent sends. AG2's contract
  here is clean enough to persist through: the hook receives the outgoing message (a `str`,
  or a dict with a `content` key) and returns it unchanged after persisting it as an
  `assistant` message. This completes the loop -- previously (manual invocation only),
  persisting the assistant's reply required an explicit `add_messages()` call.

Both hooks wrap their entire body in `try`/`except`, so a Zep outage never breaks the
agent's conversation loop -- on failure, the incoming hook simply skips the system-message
update and the outgoing hook skips persistence, in both cases still returning the message
unchanged.

`attach_to_agent` is optional and additive: `enrich_system_message`/`add_messages` remain
available for manual, explicit control (e.g. if you want to persist only some turns, or
inject context at a different point than "on receive").

### Multi-agent caveat: one manager per Zep thread

**Attach `attach_to_agent` to exactly one agent per Zep thread -- normally the user-facing
agent.** Each attached manager registers *both* hooks, so if two agents in a two-agent
conversation each call `attach_to_agent` with managers pointing at the **same
`thread_uuid`**, every turn is persisted **twice with conflicting roles**: agent A's outgoing
hook (`process_message_before_send`) persists its reply as `role="assistant"`, and agent B's
incoming hook (`process_last_received_message`) persists that *same* content again as
`role="user"` (and vice versa for the reverse direction). This silently doubles and
mislabels the thread's history -- it is not caught or deduplicated by the package.

Correct wiring -- attach only to the user-facing agent, leaving the other agent unmanaged:

```python
manager = ZepMemoryManager(zep, user_uuid, thread_uuid, graph_uuid=graph_uuid)

# Attach to ONE agent only (e.g. the user-facing assistant) ...
manager.attach_to_agent(assistant)

# ... and leave the other agent in the conversation unattached.
# Do NOT also call some_other_manager.attach_to_agent(other_agent) with a
# manager pointing at the same thread_uuid.
```

If both agents genuinely need their own automatic loop, create a second thread and give
each manager a **distinct `thread_uuid`** instead of sharing one thread:

```python
assistant_thread = await create_thread(zep, user_uuid=user_uuid)
critic_thread = await create_thread(zep, user_uuid=user_uuid)

assistant_manager = ZepMemoryManager(zep, user_uuid, assistant_thread.uuid_)
assistant_manager.attach_to_agent(assistant)

critic_manager = ZepMemoryManager(zep, user_uuid, critic_thread.uuid_)
critic_manager.attach_to_agent(critic)
```

## Provisioning

Create the Zep user and thread out-of-band, before the first turn. `ZepMemoryManager` takes
the UUIDs of resources that already exist, and it never creates a resource on a memory path.
Both helpers raise on every failure, so a misconfiguration is caught early. Pass
`first_name`, `last_name`, and `email` so Zep can anchor the identity node of the user in
the graph, and `on_created` to run one-time setup (ontology, custom instructions) after the
user is created:

```python
from zep_ag2 import create_thread, create_user


async def setup_new_user(
    zep, user_uuid: str
) -> None: ...  # example one-time setup, such as an ontology


user = await create_user(
    zep,
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_new_user,
)
thread = await create_thread(zep, user_uuid=user.uuid_)

manager = ZepMemoryManager(zep, user.uuid_, thread.uuid_, graph_uuid=user.graph_uuid)
```

The manager needs the UUID of the graph of the user for graph search. Pass `graph_uuid` from
`user.graph_uuid`. When you do not pass it, the manager reads it one time with
`user.get(user_uuid)` and caches it.

**Per-user manager instances.** Like the sibling Zep integrations, a `ZepMemoryManager` is
scoped to one `(user_uuid, thread_uuid)` pair for the lifetime of the instance -- create one
manager per user/thread rather than sharing a single instance across users.

## Custom context retrieval with `context_builder`

By default, context is retrieved via `thread.get_context(...)` (or, inside
`process_user_message`, via `thread.add_messages(..., return_context=True)`). Pass
`context_builder` to replace this with custom logic -- e.g. a filtered graph search, or a
different graph entirely:

```python
from zep_ag2.memory import ContextInput


async def my_builder(ctx: ContextInput) -> str | None:
    user = await ctx.zep.user.get(ctx.user_uuid)
    pager = await ctx.zep.graph.search_edges(
        user.graph_uuid,
        query=ctx.user_message,
    )
    edges = pager.items or []
    if not edges:
        return None
    return "\n".join(edge.fact for edge in edges if edge.fact)


manager = ZepMemoryManager(
    zep,
    user_uuid,
    thread_uuid,
    context_builder=my_builder,
)
```

`ContextInput.agent` is the AG2 agent in scope when the builder is invoked via
`attach_to_agent`'s automatic loop, and `None` otherwise (e.g. a manual
`process_user_message`/`enrich_system_message` call with no agent involved). If the builder
raises, a warning is logged and context injection is skipped for that call -- the builder
never raises into `process_user_message`/`get_memory_context`/`enrich_system_message`.

**Concurrency.** Inside `process_user_message`, when `context_builder` is set, persistence
(`thread.add_messages` *without* `return_context`) and the builder run concurrently via
`asyncio.gather(..., return_exceptions=True)`, with per-side isolation: a builder failure
never blocks the message from being persisted, and a persistence failure never prevents the
builder's context from being returned.

## Customizing the injected context template

The retrieved context (whether from the default retrieval or a `context_builder`) is wrapped
in `context_template` before being injected into the agent's system message. Override it
with your own wording, as long as it contains a literal `{context}` placeholder:

```python
manager = ZepMemoryManager(
    zep,
    user_uuid,
    thread_uuid,
    context_template="Relevant background:\n{context}",
)
```

The template is rendered via `template.replace("{context}", context_text)` -- never
`str.format` -- so context text containing `{`, `}`, or `%` is always safe to inject.

## API Reference

### ZepMemoryManager

Manages Zep memory for AG2 agents via system message injection and an optional automatic
loop.

- `ZepMemoryManager(client, user_uuid, thread_uuid=None, *, graph_uuid=None, context_builder=None, context_template=DEFAULT_CONTEXT_TEMPLATE)` — Initialize with Zep client
- `attach_to_agent(agent)` — Register the automatic inject+persist loop (see above)
- `await process_user_message(user_message, *, agent=None)` — Persist a user turn and retrieve context in one call
- `await resolve_graph_uuid()` — Return the UUID of the graph of the user, and cache it
- `await enrich_system_message(agent, query=None, limit=5)` — Inject memory context
- `await get_memory_context(query=None, limit=5)` — Get formatted context string
- `await add_messages(messages)` — Store messages in Zep thread
- `await get_session_facts()` — Get extracted session facts

### ZepGraphMemoryManager

Manages Zep knowledge graph for AG2 agents.

- `ZepGraphMemoryManager(client, graph_uuid)` — Initialize with the UUID of a graph
- `await search(query, limit=5, scope="edges")` — Search the graph
- `await add_data(data, data_type="text")` — Add data to the graph
- `await enrich_system_message(agent, query=None, limit=5)` — Inject graph context

### Provisioning

- `await create_user(client, *, user_id=None, first_name=None, last_name=None, email=None, on_created=None)` — Create a Zep user and return it; read `user.uuid_` and `user.graph_uuid`
- `await create_thread(client, *, user_uuid, thread_id=None)` — Create a Zep thread and return it; read `thread.uuid_`

### Tool Factories

All tool factories return **synchronous** callables (AG2 executes tools synchronously).
Internally they bridge to the async Zep SDK on a single shared background event loop,
reusing the `AsyncZep` client you pass in (no per-call client construction).

`create_search_memory_tool` and `create_search_graph_tool` follow a **pin-or-expose**
pattern: every search parameter (`scope`, `reranker`, `limit`, `mmr_lambda`,
`center_node_uuid`) is exposed to the model by default. The `scope` selects the v4 search
method: `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, or `auto`. The
`auto` scope calls `graph.get_context`, which accepts neither a reranker nor a limit. Use `pinned_params` to fix a
parameter to a constant (hidden from the model), or `hidden_params` to remove it from the
schema without pinning (Zep's own default applies):

```python
# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely (all exposed by default)
tool = create_search_graph_tool(zep, graph_uuid)

# Pin scope to "nodes" and limit to 5 -- hidden from the model, always sent as given
tool = create_search_graph_tool(zep, graph_uuid, pinned_params={"scope": "nodes", "limit": 5})

# Hide reranker entirely -- omitted from the schema AND the SDK call (Zep's default applies)
tool = create_search_graph_tool(zep, graph_uuid, hidden_params={"reranker"})
```

- `create_search_memory_tool(client, graph_uuid, *, pinned_params=None, hidden_params=None, filters=None, bfs_origin_node_uuids=None, scope=None, limit=None)` — Search conversation memory
- `create_add_memory_tool(client, graph_uuid, thread_uuid=None)` — Add conversation memory
- `create_search_graph_tool(client, graph_uuid, *, pinned_params=None, hidden_params=None, filters=None, bfs_origin_node_uuids=None, scope=None, limit=None)` — Search knowledge graph
- `create_add_graph_data_tool(client, graph_uuid)` — Add graph data
- `register_all_tools(agent, executor, client, graph_uuid, thread_uuid=None, memory_graph_uuid=None)` — Register all tools at once

## Examples

- **[Basic Memory](examples/ag2_basic.py)** — System message injection + memory tools
- **[Graph Memory](examples/ag2_graph.py)** — Knowledge graph with ZepGraphMemoryManager
- **[Search Tools](examples/ag2_tools_search.py)** — Read-only search tool registration
- **[Full Tools](examples/ag2_tools_full.py)** — All tools in a GroupChat with multiple agents
- **[Manual Test](examples/manual_test.py)** — End-to-end integration test with real APIs

## Configuration

### Environment Variables

```bash
# Required
export ZEP_API_KEY="your-zep-cloud-api-key"

# Required for examples that use LLM
export OPENAI_API_KEY="your-openai-api-key"
```

## Development

```bash
make install        # Install dev dependencies
make pre-commit     # Format, lint, type-check, test
make ci             # CI validation
```

## Requirements

- Python 3.11+
- `ag2>=0.9.0`
- `zep-cloud==4.0.0a5`

## License

Apache-2.0 — see [LICENSE](../../../LICENSE) for details.

## Support

- [Zep Documentation](https://help.getzep.com)
- [AG2 Documentation](https://docs.ag2.ai)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
