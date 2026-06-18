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

1. **Extracts** the latest user message from `context.input_messages`.
2. **Creates** the Zep user and thread lazily on first use (cached thereafter).
3. **Persists** the message via `thread.add_messages(return_context=True)` — storing the message and retrieving Zep's Context Block in a single round-trip.
4. **Injects** the returned Context Block (facts, relationships, prior knowledge from the whole user graph) into the model's instructions via `context.extend_instructions(...)`.

### after_run

Runs after the model responds. It reads the assistant reply from `context.response.messages` and persists it to the same Zep thread, so both sides of the conversation are captured.

Because `thread.get_user_context` (and `add_messages(return_context=True)`) assemble context from the **entire user graph**, the thread only scopes relevance — an agent on a new thread still recalls facts the same user shared earlier.

## Identity and Threads

Memory is scoped per `ZepContextProvider` instance to one `user_id` + `thread_id`. For a multi-user application, construct one provider (and one agent, or one agent per request) per user/conversation, passing real names so Zep can resolve the user's identity node in the graph.

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
| `source_id` | Optional | `"zep"` | Agent Framework attribution ID for injected instructions |
| `ignore_roles` | Optional | `None` | Roles to exclude from graph ingestion (still stored in thread history) |
| `on_user_created` | Optional | `None` | Async hook run once after a new user is created (ontology / instructions setup) |

## Features

- **Native context-provider hook** — uses Agent Framework's own `before_run` / `after_run` pipeline, the same surface as the framework's built-in memory providers.
- **Single round-trip** — persists the user turn and retrieves the Context Block in one call.
- **Lazy resource creation** — the Zep user and thread are created on first run and cached.
- **Whole-user-graph recall** — context is fused across all of the user's threads and data.
- **Per-user setup hook** — `on_user_created` for configuring ontology, custom instructions, or user summary instructions.
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

Live integration tests run only when `ZEP_API_KEY` and `OPENAI_API_KEY` are set:

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
