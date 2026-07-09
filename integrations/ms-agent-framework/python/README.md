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

## Quick Start

Attach a `ZepContextProvider` to an agent through the `context_providers` keyword argument:

```python
import asyncio
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from zep_cloud.client import AsyncZep
from zep_ms_agent_framework import ZepContextProvider

zep = AsyncZep(api_key="your-zep-api-key")

agent = Agent(
    OpenAIChatClient(model="gpt-4o-mini"),
    instructions="You are a helpful assistant with long-term memory.",
    context_providers=[
        ZepContextProvider(
            zep_client=zep,
            user_id="user-123",
            thread_id="thread-abc",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",  # optional
        )
    ],
)

async def main() -> None:
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
3. **Creates** the Zep user and thread lazily on first use (cached thereafter) via [`ensure_user`/`ensure_thread`](#provisioning).
4. **Persists** the message — via `thread.add_messages(return_context=True)` by default (a single round-trip), or concurrently with a custom `context_builder` if one is set (see [Custom context building](#custom-context-building)).
5. **Injects** the resulting context block, wrapped in `context_template`, into the model's instructions via `context.extend_instructions(...)`.

### after_run

Runs after the model responds. It reads the assistant reply from `context.response.messages` and persists it to the same Zep thread, so both sides of the conversation are captured.

Because `thread.get_user_context` (and `add_messages(return_context=True)`) assemble context from the **entire user graph**, the thread only scopes relevance — an agent on a new thread still recalls facts the same user shared earlier.

## Custom context building

Set `context_builder` on `ZepContextProvider` to replace the default context retrieval with custom logic — for example, searching a different graph, applying filters, or combining multiple sources:

```python
from zep_ms_agent_framework import ContextInput, ZepContextProvider

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

provider = ZepContextProvider(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    context_builder=my_builder,
)
```

`ContextInput` bundles `zep` (the `AsyncZep` client), `user_id`, `thread_id`, `user_message`, and `session_context` (the Agent Framework `SessionContext` for the turn).

When `context_builder` is set, message persistence (`add_messages` without `return_context`) and the builder run **concurrently**, with per-side failure isolation:

- If the builder raises, a warning is logged and context injection is skipped for that turn — but persistence still completes and the turn is marked as persisted.
- If persistence raises, a warning is logged and the turn is **not** marked as persisted (so `after_run` skips writing the assistant reply, and the turn can be retried next invocation) — but a successful builder result is still injected.

## `context_template`

Controls how retrieved context is wrapped before injection. Must contain a literal `{context}` placeholder, rendered via plain string replacement (`template.replace("{context}", context)`, never `str.format`) — so context text containing `{`, `}`, or `%` is always safe to inject:

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    context_template="Relevant memory:\n{context}",
)
```

Defaults to `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block — the same canonical wording used across zep-adk's Python, Go, and TypeScript implementations, and identical to this package's previous hardcoded output.

## Provisioning

`ensure_user` and `ensure_thread` (in `zep_ms_agent_framework.provisioning`) explicitly provision the Zep user and thread out-of-band, before the first run — useful for onboarding flows that want genuine failures (auth, network, 5xx) to raise loudly rather than degrade silently:

```python
from zep_ms_agent_framework import ensure_thread, ensure_user

async def setup_user(zep_client, user_id: str) -> None:
    ...  # e.g. configure per-user ontology

created = await ensure_user(
    zep,
    user_id="user-123",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,  # fires exactly once, only on real creation
)
await ensure_thread(zep, thread_id="thread-abc", user_id="user-123")
```

Both are create-then-catch-conflict: they call the Zep SDK's create method directly and treat an "already exists" conflict as success (returning `False`), while genuine failures propagate. If `on_created` raises, that exception also propagates even though the user was created — make the hook idempotent so it can be safely re-run.

You do not have to call these explicitly: `ZepContextProvider.before_run` calls the same logic lazily on the turn path, but wrapped so that a genuine failure there (including an `on_created` hook failure) is logged and degrades to skipping that turn rather than breaking the run. Contrast this with calling `ensure_user`/`ensure_thread` directly, out-of-band, where the same failures propagate to the caller.

## `create_zep_search_tool` / `expose_search_tool`

`create_zep_search_tool` (in `zep_ms_agent_framework.search`) returns a model-callable `agent_framework.FunctionTool` over `graph.search`. The model decides when to search the knowledge graph for specific facts, entities, or prior episodes. By default it searches the given user's graph; pass `graph_id=...` to target a shared standalone graph (e.g. a documentation knowledge base) instead.

The easiest way to use it is `expose_search_tool=True` on `ZepContextProvider`, which builds the tool once at construction and registers it on every run via `context.extend_tools(...)`:

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    expose_search_tool=True,
    search_pinned_params={"scope": "nodes", "limit": 5},
)
```

**Pin-or-expose.** Every search parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) is exposed to the model in the tool's JSON schema by default, with documented defaults. Use `search_pinned_params` to fix a parameter to a constant value and hide it from the schema; use `search_hidden_params` to hide a parameter *without* pinning it, so Zep's own server-side default applies:

```python
from zep_ms_agent_framework.search import create_zep_search_tool

# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_zep_search_tool(zep_client=zep, user_id="user-123")

# Pin scope to "nodes" and limit to 5 -- hidden from the model, always sent.
tool = create_zep_search_tool(
    zep_client=zep, user_id="user-123",
    search_pinned_params={"scope": "nodes", "limit": 5},
)

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_zep_search_tool(
    zep_client=zep, user_id="user-123", search_hidden_params={"mmr_lambda"},
)
```

`search_filters` and `bfs_origin_node_uuids` are always constructor-only (their complex shapes are not exposed to the model).

## Identity and Threads

Memory is scoped per `ZepContextProvider` instance to one `user_id` + `thread_id`. For a multi-user application, construct one provider (and one agent, or one agent per request) per user/conversation, passing real names so Zep can resolve the user's identity node in the graph.

> **Per-run identity is bound at construction, not resolved per-run.** The Agent Framework's `AgentSession`/`SessionContext` carry no `user_id`-shaped field and there is no framework convention for stashing identity in `session.state` (unlike e.g. Google ADK's `tool_context.state["zep_user_id"]` pattern) -- this was investigated and is documented in the `ZepContextProvider` class docstring. If a future Agent Framework release adds per-run identity, this is the extension point to revisit.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `zep_client` | Yes | — | Initialised `AsyncZep` client (caller owns its lifecycle) |
| `user_id` | Yes | — | Zep user ID this provider's memory is scoped to |
| `thread_id` | Yes | — | Zep thread ID the conversation is recorded in |
| `first_name` | Recommended | `None` | User first name — helps Zep anchor identity |
| `last_name` | Optional | `None` | User last name |
| `email` | Optional | `None` | User email |
| `user_message_name` | Optional | full name | Display name on persisted user messages |
| `assistant_message_name` | Optional | `"Assistant"` | Display name on persisted assistant messages |
| `source_id` | Optional | `"zep"` | Agent Framework attribution ID for injected instructions/tools |
| `ignore_roles` | Optional | `None` | Roles to exclude from graph ingestion (still stored in thread history) |
| `on_user_created` | Optional | `None` | Async hook run once after a new user is created (ontology / instructions setup); see [Provisioning](#provisioning) |
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
- **Lazy resource creation** — the Zep user and thread are created on first run and cached, via the same `ensure_user`/`ensure_thread` helpers available for out-of-band provisioning.
- **Whole-user-graph recall** — context is fused across all of the user's threads and data.
- **Per-user setup hook** — `on_user_created` for configuring ontology, custom instructions, or user summary instructions.
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
- `zep-cloud>=3.23.0`
- `agent-framework-core>=1.8.1`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see [LICENSE](../../../LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
