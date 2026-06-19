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
from pydantic_ai.capabilities import ProcessHistory
from zep_cloud.client import AsyncZep
from zep_pydantic_ai import (
    ZepDeps,
    zep_history_processor,
    create_zep_search_tool,
    persist_run,
)

zep = AsyncZep(api_key="your-zep-api-key")

agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=ZepDeps,
    capabilities=[ProcessHistory(zep_history_processor)],
    tools=[create_zep_search_tool()],
    instructions="You are a helpful assistant with long-term memory.",
)

async def main() -> None:
    deps = ZepDeps(
        client=zep,
        user_id="user_123",
        thread_id="thread_abc",
        first_name="Jane",
        last_name="Smith",
    )
    result = await agent.run("What did I tell you about my project?", deps=deps)
    print(result.output)
    # Persist the assistant's reply (the user turn was already persisted).
    await persist_run(deps, result.new_messages())

asyncio.run(main())
```

## How It Works

The integration plugs into Pydantic AI through three components.

### `ZepDeps`

A dataclass used as the agent's `deps_type`. It carries the Zep client and the
user/thread identity (plus optional name, email, and display names). Construct
one per conversation and pass it to `agent.run(..., deps=deps)`; the history
processor and the search tool both reach it via `RunContext.deps`. The Zep user
and thread are created lazily on first use -- you do not have to pre-create
them.

### `zep_history_processor`

Registered via `capabilities=[ProcessHistory(zep_history_processor)]`. Pydantic
AI runs a history processor immediately before **every** model request. On the
user's turn this processor:

1. resolves the Zep client and identity from `ctx.deps`;
2. lazily creates the Zep user and thread;
3. persists the latest user message via `thread.add_messages(return_context=True)` -- folding the write and context retrieval into a single round-trip;
4. prepends Zep's returned context block to the message history as a system message.

A subtle but important detail: `ProcessHistory` fires **once per model request,
not once per run**. A single
`agent.run` that makes a tool call invokes the processor more than once with the
same user turn. The processor therefore **dedupes by the latest user message
text** per `(user_id, thread_id)`: it persists and retrieves on the first sight
of a turn, caches the context, and replays the cached context on re-invocations
without writing to Zep again. This prevents duplicate episodes.

### `create_zep_search_tool`

A factory that returns a model-callable `@agent.tool` over `graph.search`. The
model decides when to search the knowledge graph for specific facts, entities,
or prior episodes. By default it searches the current user's graph; pass
`graph_id=...` to target a shared standalone graph (e.g. a documentation
knowledge base). Search parameters (`scope`, `reranker`, `limit`) are pinned at
construction time.

### `persist_run`

Call after `agent.run` with `result.new_messages()` to persist the assistant's
reply to the Zep thread. Only assistant text is sent -- the user turn (already
persisted by the processor) and any tool-call/tool-return scaffolding are
skipped, so Zep sees one clean assistant message per turn.

## Public API

### `ZepDeps`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `client` | `AsyncZep` | Yes | -- | Initialised Zep async client (caller owns its lifecycle) |
| `user_id` | `str` | Yes | -- | Zep user ID (one user graph) |
| `thread_id` | `str` | Yes | -- | Zep thread ID for the conversation |
| `first_name` | `str` | No | `None` | User first name (recommended; anchors the user node) |
| `last_name` | `str` | No | `None` | User last name |
| `email` | `str` | No | `None` | User email (helps identity resolution) |
| `user_name` | `str` | No | `None` | Display name for persisted user messages (defaults to first + last) |
| `assistant_name` | `str` | No | `"Assistant"` | Display name for persisted assistant messages |
| `ignore_roles` | `list[str]` | No | `None` | Roles to exclude from graph ingestion |

### `create_zep_search_tool`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `graph_id` | `str` | `None` | Standalone graph to search; when unset, searches the current user's graph |
| `scope` | `"edges" \| "nodes" \| "episodes" \| "observations" \| "thread_summaries" \| "auto"` | `"edges"` | What to search |
| `reranker` | `"rrf" \| "mmr" \| "node_distance" \| "episode_mentions" \| "cross_encoder"` | `"rrf"` | Result ordering (ignored for `scope="auto"`) |
| `limit` | `int` | `10` | Maximum results (clamped to Zep's ceiling of 50) |
| `name` | `str` | `"zep_search"` | Tool name exposed to the model |

## Features

- **Native `ProcessHistory` capability** -- the current Pydantic AI hook, not the deprecated `history_processors=` kwarg
- **Single round-trip** -- persist + retrieve context in one `add_messages` call
- **Once-per-request dedupe** -- correct under tool-calling runs that re-invoke the processor
- **Lazy resource creation** -- Zep user and thread created on first use
- **On-demand graph search** -- model-callable tool over `graph.search`
- **Graceful error handling** -- Zep failures are logged but never crash the agent run
- **Fully typed** -- ships type hints; passes `mypy --strict`-style checks

## Error Handling

Every Zep call is wrapped: a Zep outage, auth failure, or transient error is
logged and the agent run continues. When persistence fails the turn is not
cached, so the next model request retries it.

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
- `zep-cloud>=3.23.0`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Pydantic AI Documentation](https://ai.pydantic.dev)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 - see [LICENSE](../../../LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
