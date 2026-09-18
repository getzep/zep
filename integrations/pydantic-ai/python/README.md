# Zep Pydantic AI Integration

A memory integration package that gives [Pydantic AI](https://ai.pydantic.dev) agents long-term memory powered by [Zep](https://www.getzep.com). User turns are persisted to Zep and relevant context from Zep's temporal knowledge graph is injected into the model prompt on every turn -- using Pydantic AI's native `ProcessHistory` capability -- plus an on-demand graph-search tool.

## Installation

```bash
pip install zep-pydantic-ai
```

See [SETUP.md](SETUP.md) for how to sign up for Zep, create an API key, configure your environment, and run the example.

## Quick Start

```python
import asyncio
from pydantic_ai import Agent
from zep_cloud.client import AsyncZep
from zep_pydantic_ai import (
    ZepDeps,
    create_thread,
    create_user,
    create_zep_search_tool,
    zep_capabilities,
)

zep = AsyncZep(api_key="your-zep-api-key")

# Create the Zep resources one time, and store the returned UUIDs in your own
# database. Zep v4 addresses every user, thread, and graph by a UUID.
user = await create_user(zep, first_name="Jane", last_name="Smith")
thread = await create_thread(zep, user_uuid=user.uuid_)

deps = ZepDeps(
    client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
    first_name="Jane",
    last_name="Smith",
)

agent = Agent(
    "openai:gpt-5-mini",
    deps_type=ZepDeps,
    capabilities=zep_capabilities(deps),
    tools=[create_zep_search_tool()],
    instructions="You are a helpful assistant with long-term memory.",
)


async def main() -> None:
    result = await agent.run("What did I tell you about my project?", deps=deps)
    print(result.output)
    # The user turn and the assistant's reply are both already persisted --
    # zep_capabilities(deps) wires in automatic assistant persistence.


asyncio.run(main())
```

Prefer explicit control over when the assistant's reply is persisted? Use
`capabilities=[ProcessHistory(zep_history_processor)]` and call `persist_run`
yourself -- see [`persist_run`](#persist_run) below.

## How It Works

### `ZepDeps`

A dataclass used as the agent's `deps_type`. It carries the Zep client, the
user UUID, the thread UUID, and the optional graph UUID (plus optional names
and display names). Construct one per conversation and pass it to
`agent.run(..., deps=deps)`; the history processor and the search tool both
reach it via `RunContext.deps`. The Zep user and the thread must exist before
the first turn. Create them with `create_user` and `create_thread`, and store
the returned UUIDs (see [Provisioning](#provisioning) below).

### `zep_history_processor`

Registered via `capabilities=[ProcessHistory(zep_history_processor)]` (or via
`zep_capabilities(deps)`, which includes it). Pydantic AI runs a history
processor immediately before **every** model request. On the user's turn this
processor:

1. resolves the Zep client and the UUIDs from `ctx.deps`;
2. persists the latest user message -- via `thread.add_messages(return_context=True)` by default (a single round-trip), or concurrently with a custom `context_builder` if one is set (see [Custom context building](#custom-context-building));
3. prepends the resulting context block, wrapped in `context_template`, to the message history as a system message.

A subtle but important detail: `ProcessHistory` fires **once per model request,
not once per run**. A single `agent.run` that makes a tool call invokes the
processor more than once with the same user turn. The processor therefore
**dedupes per run**: it persists and retrieves on the first model request of a
run (keyed by Pydantic AI's `RunContext.run_id`), caches the result, and
replays it on re-invocations within the same run without touching Zep again.
Two separate runs that send identical text each persist -- dedupe is scoped to
the run, not the text.

### Custom context building

Set `context_builder` on `ZepDeps` to replace the default context retrieval
with custom logic -- for example, searching a different graph, applying
filters, or combining multiple sources:

```python
from zep_pydantic_ai import ContextInput, ZepDeps


async def my_builder(ctx: ContextInput) -> str | None:
    if ctx.graph_uuid is None:
        return None
    pager = await ctx.zep.graph.search_edges(
        ctx.graph_uuid,
        query=ctx.user_message,
    )
    edges = pager.items or []
    if not edges:
        return None
    return "\n".join(edge.fact for edge in edges)


deps = ZepDeps(
    client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
    context_builder=my_builder,
)
```

`ContextInput` bundles `zep` (the `AsyncZep` client), `user_uuid`,
`thread_uuid`, `graph_uuid`, `user_message`, and `run_context` (the Pydantic
AI `RunContext` for the turn).

When `context_builder` is set, message persistence (`add_messages` without
`return_context`) and the builder run **concurrently**, with per-side failure
isolation:

- If the builder raises, a warning is logged and context injection is skipped
  for that turn -- but persistence still completes.
- If persistence raises, a warning is logged and the turn is **not** marked as
  persisted (so it retries on the next model request) -- but a successful
  builder result is still injected.

### `context_template`

Controls how retrieved context is wrapped before injection. Must contain a
literal `{context}` placeholder, rendered via plain string replacement
(`template.replace("{context}", context)`, never `str.format`) -- so context
text containing `{`, `}`, or `%` is always safe to inject:

```python
deps = ZepDeps(
    client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    context_template="Relevant memory:\n{context}",
)
```

Defaults to `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>`
block -- the same canonical wording used across zep-adk's Python, Go, and
TypeScript implementations.

### Provisioning

`create_user` and `create_thread` (in `zep_pydantic_ai.provisioning`)
provision the Zep user and the thread out-of-band, before the first turn. A
create call does not carry an application identifier. The response carries the
server-generated UUID that every later call uses:

```python
from zep_pydantic_ai import create_thread, create_user

user = await create_user(
    zep,
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
)
thread = await create_thread(zep, user_uuid=user.uuid_)

# Store these values in your own database.
user_uuid = user.uuid_
graph_uuid = user.graph_uuid
thread_uuid = thread.uuid_
```

Both helpers raise on failure, because provisioning runs before the first turn
and a misconfiguration must be visible there. The integration never resolves a
name to a UUID at run time. Your application does the resolution one time and
stores the UUIDs.

### `create_zep_search_tool`

A factory that returns a model-callable `pydantic_ai.Tool` over the Zep v4
graph search methods. The model decides when to search the knowledge graph for
specific facts, entities, or prior episodes. By default it searches the graph
of `ZepDeps.graph_uuid`; pass `graph_uuid=...` to target a shared standalone
graph (e.g. a documentation knowledge base).

**Pin-or-expose.** Every search parameter (`scope`, `reranker`, `limit`,
`mmr_lambda`, `center_node_uuid`) is exposed to the model in the tool's JSON
schema by default, with documented defaults. Use `pinned_params` to fix a
parameter to a constant value and hide it from the schema; use `hidden_params`
to hide a parameter *without* pinning it, so Zep's own server-side default
applies:

```python
# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_zep_search_tool()

# Pin scope to "nodes" and limit to 5 -- hidden from the model, always sent.
tool = create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5})

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_zep_search_tool(hidden_params={"mmr_lambda"})
```

`filters` and `bfs_origin_node_uuids` are always constructor-only
(their complex shapes are not exposed to the model).

### `persist_run`

Call after `agent.run` with `result.new_messages()` to persist the assistant's
reply to the Zep thread. Only assistant text is sent -- the user turn (already
persisted by the processor) and any tool-call/tool-return scaffolding are
skipped, so Zep sees one clean assistant message per turn. Not needed if you
use `zep_capabilities(deps)` (see below).

### `zep_capabilities` / automatic assistant persistence

`zep_capabilities(deps)` bundles `ProcessHistory(zep_history_processor)` with
an `after_run` hook (via Pydantic AI's `Hooks` capability) that automatically
persists the assistant's reply once the run completes -- no explicit
`persist_run` call needed:

```python
from zep_pydantic_ai import zep_capabilities

agent = Agent(
    "openai:gpt-5-mini",
    deps_type=ZepDeps,
    capabilities=zep_capabilities(deps),
)

result = await agent.run("Hi", deps=deps)
# Both sides of the turn are already in Zep.
```

Use `create_zep_after_run_hook(deps)` directly if you want to compose it with
your own `Hooks(...)` instance instead of using the bundled list.

## Public API

### `ZepDeps`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `client` | `AsyncZep` | Yes | -- | Initialised Zep async client (caller owns its lifecycle) |
| `user_uuid` | `str` | Yes | -- | Zep user UUID |
| `thread_uuid` | `str` | Yes | -- | Zep thread UUID for the conversation |
| `graph_uuid` | `str \| None` | No | `None` | UUID of the user's graph; the search tool uses it |
| `first_name` | `str` | No | `None` | User first name (recommended; anchors the user node) |
| `last_name` | `str` | No | `None` | User last name |
| `user_name` | `str` | No | `None` | Display name for persisted user messages (defaults to first + last) |
| `assistant_name` | `str` | No | `"Assistant"` | Display name for persisted assistant messages |
| `ignore_roles` | `list[str]` | No | `None` | Roles to exclude from graph ingestion |
| `context_builder` | `ContextBuilder \| None` | No | `None` | Custom async context-retrieval callable; see [Custom context building](#custom-context-building) |
| `context_template` | `str` | No | `DEFAULT_CONTEXT_TEMPLATE` | Template wrapping injected context; see [`context_template`](#context_template) |

### `create_user`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `client` | `AsyncZep` | -- | Initialised Zep async client |
| `first_name` | `str \| None` | `None` | Passed through to `user.create` |
| `last_name` | `str \| None` | `None` | Passed through to `user.create` |
| `email` | `str \| None` | `None` | Passed through to `user.create` |

Returns the created `User`. Read `uuid_` and `graph_uuid` from it, and store
both values. Failures propagate.

### `create_thread`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `client` | `AsyncZep` | -- | Initialised Zep async client |
| `user_uuid` | `str` | -- | The UUID of the user that owns the thread |

Returns the created `Thread`. Read `uuid_` from it, and store the value.
Failures propagate.

### `create_zep_search_tool`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `graph_uuid` | `str \| None` | `None` | Standalone graph UUID to search; when unset, the tool uses `ZepDeps.graph_uuid` |
| `pinned_params` | `dict[str, Any] \| None` | `None` | Fix a search parameter to a value; hidden from the model schema |
| `hidden_params` | `set[str] \| None` | `None` | Hide a search parameter from the schema without pinning (Zep's default applies) |
| `filters` | `dict[str, Any] \| None` | `None` | Constructor-only Zep search filters (`node_labels`, `edge_types`, etc.) |
| `bfs_origin_node_uuids` | `list[str] \| None` | `None` | Constructor-only node UUIDs for BFS seeding |
| `name` | `str` | `"zep_search"` | Tool name exposed to the model |
| `description` | `str` | (see source) | Tool description exposed to the model |
| `scope` | `Scope \| None` | `None` | Back-compat alias for `pinned_params={"scope": scope}` |
| `reranker` | `Reranker \| None` | `None` | Back-compat alias for `pinned_params={"reranker": reranker}` |
| `limit` | `int \| None` | `None` | Back-compat alias for `pinned_params={"limit": limit}` (clamped to Zep's ceiling of 50) |

Model-exposed search parameters (when not pinned/hidden), with their defaults:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scope` | `"edges" \| "nodes" \| "episodes" \| "observations" \| "thread_summaries" \| "auto"` | `"edges"` | What to search |
| `reranker` | `"rrf" \| "mmr" \| "node_distance" \| "episode_mentions" \| "cross_encoder"` | `"rrf"` | Result ordering (ignored for `scope="auto"`) |
| `limit` | `int` | `10` | Maximum results (clamped to Zep's ceiling of 50) |
| `mmr_lambda` | `float` | -- | Diversity/relevance balance; only used when `reranker="mmr"` |
| `center_node_uuid` | `str` | -- | Center node for `reranker="node_distance"` |

Returns a `pydantic_ai.Tool[ZepDeps]` -- pass it directly in `tools=[...]`.

## Features

- **Native `ProcessHistory` capability** -- the current Pydantic AI hook, not the deprecated `history_processors=` kwarg
- **Single round-trip** -- persist + retrieve context in one `add_messages` call (or concurrently, with a custom `context_builder`)
- **Once-per-run dedupe** -- correct under tool-calling runs that re-invoke the processor
- **Out-of-band provisioning** -- `create_user`/`create_thread` for onboarding flows that want failures to raise loudly
- **Pin-or-expose search tool** -- every search parameter model-exposed by default, or pinned/hidden per deployment
- **Automatic assistant persistence** -- `zep_capabilities(deps)` wires up `Hooks(after_run=...)`, or persist explicitly with `persist_run`
- **Graceful error handling** -- Zep failures are logged but never crash the agent run
- **Fully typed** -- ships type hints; passes `mypy --strict`-style checks

## Error Handling

Every Zep call on the turn path (history processor, search tool, and the
`after_run` hook) is wrapped: a Zep outage, auth failure, or transient error is
logged and the agent run continues without memory for that turn. When
persistence fails the turn is not cached, so the next model request retries
it. `create_user`/`create_thread`, called out-of-band, are the exception: they
raise genuine failures so misconfiguration is caught before the agent ever
runs.

## Configuration

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"   # or another provider supported by Pydantic AI
```

## Examples

See the [examples/](examples/) directory:

- **[basic_agent.py](examples/basic_agent.py)** -- fact seeding and memory recall with the history processor + search tool.

## Development

```bash
make install      # uv sync --extra dev
make format       # ruff format
make lint         # ruff check
make type-check   # mypy src/
make test         # pytest
make all          # format + lint + type-check + test
make build        # uv build
```

## Requirements

- Python 3.11+
- `pydantic-ai>=1.107,<2`
- `zep-cloud==4.0.0a5`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Pydantic AI Documentation](https://ai.pydantic.dev)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 - see [LICENSE](../../../LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
