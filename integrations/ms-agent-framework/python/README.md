# Zep Microsoft Agent Framework Integration

Long-term memory for [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) agents, backed by [Zep](https://www.getzep.com)'s temporal Context Graph. Persists conversation turns and injects relevant context into the model on every run.

## Installation

```bash
pip install zep-ms-agent-framework
```

The package depends only on `agent-framework-core`. The runnable example also uses a model provider:

```bash
pip install zep-ms-agent-framework agent-framework-openai
```

## Identifiers

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID. A `user_id` or a `thread_id` is a name, not an address. The provider takes `user_uuid`, `thread_uuid`, and an optional `graph_uuid`.

Create the user and the thread one time, read the UUIDs from the create responses, and store the UUIDs in your own database. The provider does not look up a UUID at run time.

## Quick Start

Attach a `ZepContextProvider` to an agent through the `context_providers` keyword argument:

```python
import asyncio
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from zep_cloud.client import AsyncZep
from zep_ms_agent_framework import ZepContextProvider, create_thread, create_user

zep = AsyncZep(api_key="your-zep-api-key")


async def main() -> None:
    # One-time provisioning. Store these UUIDs in your own database.
    user = await create_user(zep, first_name="Jane", last_name="Smith")
    thread = await create_thread(zep, user_uuid=user.uuid_)

    agent = Agent(
        OpenAIChatClient(model="gpt-5-mini"),
        instructions="You are a helpful assistant with long-term memory.",
        context_providers=[
            ZepContextProvider(
                zep_client=zep,
                user_uuid=user.uuid_,
                thread_uuid=thread.uuid_,
                graph_uuid=user.graph_uuid,
            )
        ],
    )

    result = await agent.run("Hi, I'm a data scientist in Portland.")
    print(result.text)


asyncio.run(main())
```

## How It Works

The integration ships one class — `ZepContextProvider` — that subclasses Agent Framework's [`ContextProvider`](https://github.com/microsoft/agent-framework) and overrides the two lifecycle hooks the framework calls around every `agent.run(...)`. See [`src/zep_ms_agent_framework/context_provider.py`](src/zep_ms_agent_framework/context_provider.py).

### before_run

Runs before the model is invoked. On each turn it:

1. **Registers** the graph-search tool via `context.extend_tools(...)`, if `expose_search_tool=True` (see [`create_zep_search_tool`](#create_zep_search_tool--expose_search_tool) below).
2. **Extracts** the latest user message from `context.input_messages`.
3. **Persists** the message — via `thread.add_messages(return_context=True)` by default (a single round-trip), or concurrently with a custom `context_builder` if one is set (see [Custom context building](#custom-context-building)).
4. **Injects** the resulting context block, wrapped in `context_template`, into the model's instructions via `context.extend_instructions(...)`.

### after_run

Runs after the model responds. It reads the assistant reply from `context.response.messages` and persists it to the same Zep thread, so both sides of the conversation are captured.

Because `thread.get_context` (and `add_messages(return_context=True)`) assemble context from the **entire user graph**, the thread only scopes relevance — an agent on a new thread still recalls facts the same user shared earlier.

## Custom context building

Set `context_builder` on `ZepContextProvider` to replace the default context retrieval with custom logic — for example, searching a different graph, applying filters, or combining multiple sources:

```python
from zep_ms_agent_framework import ContextInput, ZepContextProvider


async def my_builder(ctx: ContextInput) -> str | None:
    if ctx.graph_uuid is None:
        return None
    pager = await ctx.zep.graph.search_edges(
        ctx.graph_uuid,
        query=ctx.user_message,
        limit=10,
    )
    edges = pager.items or []
    if not edges:
        return None
    return "\n".join(edge.fact for edge in edges if edge.fact)


provider = ZepContextProvider(
    zep_client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
    context_builder=my_builder,
)
```

`ContextInput` bundles `zep` (the `AsyncZep` client), `user_uuid`, `thread_uuid`, `graph_uuid`, `user_message`, and `session_context` (the Agent Framework `SessionContext` for the turn).

When `context_builder` is set, message persistence (`add_messages` without `return_context`) and the builder run **concurrently**, with per-side failure isolation:

- If the builder raises, a warning is logged and context injection is skipped for that turn — but persistence still completes and the turn is marked as persisted.
- If persistence raises, a warning is logged and the turn is **not** marked as persisted (so `after_run` skips writing the assistant reply, and the turn can be retried next invocation) — but a successful builder result is still injected.

## `context_template`

Controls how retrieved context is wrapped before injection. Must contain a literal `{context}` placeholder, rendered via plain string replacement (`template.replace("{context}", context)`, never `str.format`) — so context text containing `{`, `}`, or `%` is always safe to inject:

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    context_template="Relevant memory:\n{context}",
)
```

Defaults to `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block — the same canonical wording used across zep-adk's Python, Go, and TypeScript implementations, and identical to this package's previous hardcoded output.

## Provisioning

`create_user` and `create_thread` (in `zep_ms_agent_framework.provisioning`) provision the Zep user and thread out-of-band, before the first run. The server generates the UUIDs, and the create response carries them:

```python
from zep_ms_agent_framework import create_thread, create_user


async def setup_user(zep_client, user_uuid: str) -> None: ...  # e.g. configure per-user ontology


user = await create_user(
    zep,
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,  # runs one time, after the user is created
)
thread = await create_thread(zep, user_uuid=user.uuid_)

# Store user.uuid_, user.graph_uuid, and thread.uuid_ in your own database.
```

A failure of the create call, or of the `on_created` hook, propagates to the caller. Make the hook idempotent so that you can run it again after a failure.

The provider does not create a user or a thread. Give it the UUIDs that these helpers return.

## `create_zep_search_tool` / `expose_search_tool`

`create_zep_search_tool` (in `zep_ms_agent_framework.search`) returns a model-callable `agent_framework.FunctionTool` over the v4 graph search methods. The model decides when to search the knowledge graph for specific facts, entities, or prior episodes. The tool searches the graph that `graph_uuid` identifies: give the `graph_uuid` of a user for personal memory, or the `uuid_` of a standalone graph, such as a documentation knowledge base, for shared knowledge.

The easiest way to use it is `expose_search_tool=True` on `ZepContextProvider`, which builds the tool once at construction and registers it on every run via `context.extend_tools(...)`:

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
    expose_search_tool=True,
    search_pinned_params={"scope": "nodes", "limit": 5},
)
```

`expose_search_tool=True` requires a `graph_uuid`.

**Pin-or-expose.** Every search parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) is exposed to the model in the tool's JSON schema by default, with documented defaults. Use `search_pinned_params` to fix a parameter to a constant value and hide it from the schema; use `search_hidden_params` to hide a parameter *without* pinning it, so Zep's own server-side default applies:

```python
from zep_ms_agent_framework.search import create_zep_search_tool

# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_zep_search_tool(zep_client=zep, graph_uuid=user.graph_uuid)

# Pin scope to "nodes" and limit to 5 -- hidden from the model, always sent.
tool = create_zep_search_tool(
    zep_client=zep,
    graph_uuid=user.graph_uuid,
    search_pinned_params={"scope": "nodes", "limit": 5},
)

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_zep_search_tool(
    zep_client=zep,
    graph_uuid=user.graph_uuid,
    search_hidden_params={"mmr_lambda"},
)
```

`search_filters` and `bfs_origin_node_uuids` are always constructor-only (their complex shapes are not exposed to the model).

## Identity and Threads

Memory is scoped per `ZepContextProvider` instance to one `user_uuid` + `thread_uuid`. For a multi-user application, construct one provider (and one agent, or one agent per request) per user and conversation. Give real names to `create_user` so that Zep can resolve the user's identity node in the graph.

> **Per-run identity is bound at construction, not resolved per-run.** The Agent Framework's `AgentSession`/`SessionContext` carry no identity field, and there is no framework convention for stashing identity in `session.state` (unlike e.g. Google ADK's `tool_context.state["zep_user_id"]` pattern) -- this was investigated and is documented in the `ZepContextProvider` class docstring. If a future Agent Framework release adds per-run identity, this is the extension point to revisit.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `zep_client` | Yes | — | Initialised `AsyncZep` client (caller owns its lifecycle) |
| `user_uuid` | Yes | — | UUID of the Zep user this provider's memory is scoped to |
| `thread_uuid` | Yes | — | UUID of the Zep thread the conversation is recorded in |
| `graph_uuid` | Optional | `None` | UUID of the graph to search; required when `expose_search_tool` is `True` |
| `user_message_name` | Optional | full name | Display name on persisted user messages |
| `assistant_message_name` | Optional | `"Assistant"` | Display name on persisted assistant messages |
| `source_id` | Optional | `"zep"` | Agent Framework attribution ID for injected instructions/tools |
| `ignore_roles` | Optional | `None` | Roles to exclude from graph ingestion (still stored in thread history) |
| `context_builder` | Optional | `None` | Custom async context-retrieval callable; see [Custom context building](#custom-context-building) |
| `context_template` | Optional | `DEFAULT_CONTEXT_TEMPLATE` | Template wrapping injected context; see [`context_template`](#context_template) |
| `expose_search_tool` | Optional | `False` | Register a model-callable graph-search tool every run; see [`create_zep_search_tool`](#create_zep_search_tool--expose_search_tool) |
| `search_pinned_params` | Optional | `None` | Fix a search parameter to a value; hidden from the model schema |
| `search_hidden_params` | Optional | `None` | Hide a search parameter from the schema without pinning (Zep's default applies) |
| `search_filters` | Optional | `None` | Constructor-only Zep search filters (`node_labels`, `edge_types`, etc.) |
| `bfs_origin_node_uuids` | Optional | `None` | Constructor-only node UUIDs for BFS seeding |

## Features

- **Native context-provider hook** — uses Agent Framework's own `before_run` / `after_run` pipeline, the same surface as the framework's built-in memory providers.
- **Single round-trip** — persists the user turn and retrieves the Context Block in one call (or concurrently, with a custom `context_builder`).
- **UUID addressing** — the provider takes the server-generated UUIDs and makes no lookup call on the turn path.
- **Whole-user-graph recall** — context is fused across all of the user's threads and data.
- **Per-user setup hook** — `create_user(on_created=...)` for configuring ontology, custom instructions, or user summary instructions.
- **Pin-or-expose search tool** — `expose_search_tool`/`create_zep_search_tool` for on-demand graph search, with every search parameter model-exposed by default or pinned/hidden per deployment.
- **Graceful error handling** — a Zep failure is logged but never crashes the host agent; the agent degrades to memoryless for that turn.
- **Async-only, client-agnostic** — requires `AsyncZep`; works with any Agent Framework chat client.

## Configuration

```bash
# Required
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"   # for the example / live tests
```

See [SETUP.md](SETUP.md) for signing up, creating an API key, and running the example end to end.

## Examples

- **[examples/basic_agent.py](examples/basic_agent.py)** — a single agent seeding facts in one thread and recalling them in a new thread (cross-thread recall).

## Development

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/ms-agent-framework/python
make install      # uv sync --extra dev
make all          # format + lint + type-check + test
```

| Command | Description |
|---------|-------------|
| `make format` | Format code with ruff |
| `make lint` | Run linting checks |
| `make type-check` | Run mypy type checking |
| `make test` | Run the test suite (integration tests skip without API keys) |
| `make all` | Run all checks |
| `make build` | Build the package |

Live integration tests run only when `ZEP_API_KEY` is set; the agent-driven lifecycle test additionally requires `OPENAI_API_KEY` (it is skipped, not failed, when that key is absent):

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

## Requirements

- Python 3.11+
- `zep-cloud==4.0.0a5`
- `agent-framework-core>=1.8.1`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](../../../LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
