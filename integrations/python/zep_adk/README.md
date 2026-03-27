# Zep Google ADK Integration

A memory integration package that enables [Google ADK](https://github.com/google/adk-python) agents to leverage [Zep](https://getzep.com)'s long-term memory platform for persistent conversation storage and context-aware responses.

## Installation

```bash
pip install zep-adk
```

## Quick Start

Define one agent, shared across all users. Per-user identity is passed via ADK session state.

```python
import os
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from zep_cloud.client import AsyncZep
from zep_adk import ZepContextTool, create_after_model_callback

# Initialize Zep client
zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

# One shared agent definition
agent = Agent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="You are a helpful assistant with long-term memory.",
    tools=[ZepContextTool(zep_client=zep)],
    after_model_callback=create_after_model_callback(zep_client=zep),
)

session_service = InMemorySessionService()
runner = Runner(agent=agent, app_name="my_app", session_service=session_service)

# Per-user session: user_id → Zep user, session_id → Zep thread
await session_service.create_session(
    app_name="my_app",
    user_id="user_123",          # automatically used as Zep user ID
    session_id="session_abc",    # automatically used as Zep thread ID
    state={
        "zep_first_name": "Jane",
        "zep_last_name": "Smith",
        "zep_email": "jane@example.com",  # optional
    },
)
```

## Session State Keys

Identity is resolved at runtime from ADK session state and session metadata. The ADK `user_id` is used as the Zep user ID and the `session_id` is used as the Zep thread ID -- both automatically, no state keys needed.

Set these optional keys when creating a session to enrich the Zep user profile:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `zep_first_name` | Recommended | `"Anonymous"` | User's first name. Zep uses this to anchor the user's identity node in the knowledge graph. |
| `zep_last_name` | Optional | `"User"` | User's last name. |
| `zep_email` | Optional | `None` | User's email address. |
| `zep_user_id` | Optional | ADK `user_id` | Override the Zep user ID if it differs from the ADK user ID. |
| `zep_thread_id` | Optional | ADK `session_id` | Override the Zep thread ID if it differs from the ADK session ID. |

## How It Works

The integration uses two components that work together to give your ADK agent persistent memory:

### ZepContextTool

A `BaseTool` subclass that hooks into ADK's `process_llm_request()` lifecycle method (the same pattern ADK's own `PreloadMemoryTool` uses). On every LLM turn it:

1. **Extracts** the user's latest message from the invocation context.
2. **Resolves** the user's Zep identity from session state.
3. **Persists** the message to Zep via `thread.add_messages(return_context=True)` -- storing the message and retrieving relevant context in a single API call.
4. **Injects** the returned context (facts, relationships, prior knowledge) into the LLM's system instructions.

The tool is never called by the model directly; it modifies the outgoing LLM request before it is sent.

### create_after_model_callback

A factory function that returns an `after_model_callback` for persisting assistant responses to Zep. This ensures both sides of the conversation are stored in Zep's memory. The callback also resolves the thread ID from session state at runtime.

Both components include per-thread message deduplication to handle ADK's tool-use cycles, where the framework may call hooks multiple times per turn.

## Adding Zep to an Existing Agent

If you already have an ADK agent serving all users, adding Zep memory requires only three changes -- no restructuring needed:

1. **Add `ZepContextTool` to your agent's tools list:**

```python
from zep_adk import ZepContextTool

agent = Agent(
    name="my_existing_agent",
    model="gemini-2.5-flash",
    instruction="...",
    tools=[your_existing_tool, ZepContextTool(zep_client=zep)],
    after_model_callback=create_after_model_callback(zep_client=zep),
)
```

2. **Include the user's name in session state** when creating sessions (you're already creating sessions -- just add the keys):

```python
await session_service.create_session(
    app_name="my_app",
    user_id=user_id,          # automatically used as Zep user ID
    session_id=session_id,    # automatically used as Zep thread ID
    state={
        **your_existing_state,
        "zep_first_name": first_name,
        "zep_last_name": last_name,
    },
)
```

3. That's it. No factory function, no per-session agent instances.

## Features

- **Shared-agent architecture** -- one Agent definition serves all users
- **Session-state-driven identity** -- per-user configuration via ADK's standard mechanism
- **Single round-trip** -- persist messages and retrieve context in one API call
- **Lazy resource creation** -- Zep user and thread are created on first use
- **Per-thread deduplication** -- prevents double-persistence during tool-use cycles
- **Graceful error handling** -- Zep API failures are logged but never crash the agent
- **Context injection** -- Zep's knowledge graph context is injected as system instructions
- **Per-user setup hook** -- `on_user_created` callback for configuring ontology, instructions, and summaries per user
- **On-demand graph search** -- `ZepGraphSearchTool` lets the model actively search the knowledge graph
- **Configurable graph search** -- pin parameters at construction or let the model choose

## Configuration

### Environment Variables

```bash
# Required
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

### Constructor Parameters

#### ZepContextTool

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `zep_client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `context_builder` | `ContextBuilder` | No | `None` | Custom async callable for context retrieval |
| `ignore_roles` | `list[str]` | No | `None` | Roles to exclude from graph ingestion |
| `on_user_created` | `UserSetupHook` | No | `None` | Async callback fired once after a new Zep user is created. Use for per-user ontology, custom instructions, or user summary instructions. |

#### create_after_model_callback

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `zep_client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `assistant_name` | `str` | No | `"Assistant"` | Display name for the assistant in Zep |
| `ignore_roles` | `list[str]` | No | `None` | Roles to exclude from graph ingestion |

#### ZepGraphSearchTool

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `zep_client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `graph_id` | `str` | No | `None` | Fixed graph ID for shared-graph search |
| `name` | `str` | No | `"zep_graph_search"` | Tool name visible to the model |
| `description` | `str` | No | (default) | Tool description visible to the model |
| `search_filters` | `dict` | No | `None` | Zep search filters (constructor-only) |
| `bfs_origin_node_uuids` | `list[str]` | No | `None` | BFS seed node UUIDs (constructor-only) |
| `**pinned` | any | No | -- | Pin any search param: `scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid` |

## Examples

See the [examples/](examples/) directory for complete working examples:

- **[basic_agent.py](examples/basic_agent.py)** -- Full example with fact seeding and memory recall using the shared-agent pattern

## Development

### Setup

```bash
git clone https://github.com/getzep/zep.git
cd integrations/python/zep_adk
make install
```

### Commands

```bash
make format      # Format code with ruff
make lint        # Run linting checks
make type-check  # Run mypy type checking
make test        # Run test suite
make all         # Run all checks
make pre-commit  # Development workflow with auto-fixes
make ci          # Strict CI checks
```

## Requirements

- Python 3.10+
- `zep-cloud>=3.0.0`
- `google-adk>=1.0.0`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Google ADK Documentation](https://google.github.io/adk-docs/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
