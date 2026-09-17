# Zep Google ADK Integration

A memory integration package that enables [Google ADK](https://github.com/google/adk-python) agents to leverage [Zep](https://getzep.com)'s long-term memory platform for persistent conversation storage and context-aware responses.

## Installation

```bash
pip install zep-adk
```

## Choosing a component

zep-adk ships the same set of capabilities across Python, TypeScript, and Go, though the exact symbol names differ per language's ADK idioms:

| Capability | Python | TypeScript | Go |
|---|---|---|---|
| guaranteed context injection | `ZepContextTool` | `ZepContextTool` or `createZepBeforeModelCallback` | `NewBeforeModelCallback` |
| assistant-turn persistence | `create_after_model_callback` | `createZepAfterModelCallback` | `NewAfterModelCallback` |
| explicit provisioning | `create_user`/`create_thread` | `createUser`/`createThread` | `CreateUser`/`CreateThread` |
| custom context block | `context_builder` | `contextBuilder` | `WithContextBuilder` |
| injection template | `context_template` | `contextTemplate` | `WithContextTemplate` |
| model-callable graph search (pin-or-expose, 6 scopes) | `ZepGraphSearchTool` | `ZepGraphSearchTool` | `NewGraphSearchTool` |
| ADK-native memory service | `ZepMemoryService` | `ZepMemoryService` | `NewMemoryService` |

Note: Go intentionally has no tool-based injection (callbacks are the Go-ADK-idiomatic hook).

## Identifiers

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
`user_id` or a `thread_id` is a name, not an address. The public API of this
package takes `user_uuid` and `thread_uuid`.

Provision the Zep resources one time, read the UUIDs from the responses, and
store the UUIDs in your own database. The integration does not look an
identifier up at run time.

The Zep v4 API rejects `thread.add_messages` with a 404 for a user that has no
`user_id` name. Give a `user_id` name to `create_user`. The name is a label
only; every later call uses the UUID.

## Quick Start

Define one agent, shared across all users. Per-user identity is passed via ADK session state.

```python
import os
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from zep_cloud.client import AsyncZep
from zep_adk import ZepContextTool, create_after_model_callback, create_user, create_thread

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

# Provision the Zep user and thread out-of-band, BEFORE the first turn --
# e.g. during account/session onboarding in your app. Zep generates the UUIDs.
user = await create_user(
    zep,
    user_id="user_123",  # a name, not an address
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",  # optional
)
thread = await create_thread(zep, user_uuid=user.uuid_)

# Store user.uuid_, user.graph_uuid, and thread.uuid_ in your own database, and
# put them into the ADK session state.
await session_service.create_session(
    app_name="my_app",
    user_id="user_123",
    session_id="session_abc",
    state={
        "zep_user_uuid": user.uuid_,
        "zep_thread_uuid": thread.uuid_,
        "zep_graph_uuid": user.graph_uuid,
        "zep_first_name": "Jane",
        "zep_last_name": "Smith",
    },
)
```

## Session State Keys

Identity is resolved at runtime from ADK session state. Set the Zep UUIDs when you create the session:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `zep_user_uuid` | Yes | ADK `user_id` | The UUID of the Zep user. The ADK `user_id` is used only when it holds the Zep user UUID. |
| `zep_thread_uuid` | Yes | ADK `session_id` | The UUID of the Zep thread. The ADK `session_id` is used only when it holds the Zep thread UUID. |
| `zep_graph_uuid` | Optional | resolved from the user | The UUID of the graph of the user. `ZepGraphSearchTool` and `ZepMemoryService` resolve it through `user.get` when it is absent. |
| `zep_first_name` | Recommended | `"Anonymous"` | User's first name. Attached as the author name on persisted messages so Zep can anchor them to the user's identity node in the knowledge graph. |
| `zep_last_name` | Optional | `"User"` | User's last name. |

The user's name and email on the Zep user profile itself are set during
provisioning -- pass them to `create_user`, not session state.

## How It Works

The integration uses four components that work together to give your ADK agent persistent memory:

### create_user / create_thread

Explicit, out-of-band provisioning helpers. Call these once -- during onboarding, account creation, or before the first turn of a new conversation -- **before** the agent runs. Each calls the Zep v4 create method and returns the created object, so the caller reads `user.uuid_`, `user.graph_uuid`, and `thread.uuid_`. Every call creates a new resource, thus the helpers are not idempotent. Failures (auth, network, 5xx) raise, so misconfiguration is caught immediately rather than silently swallowed.

Run one-time per-user setup -- a per-user ontology, custom instructions, or summary instructions -- directly after `create_user` returns.

The ADK turn path (`ZepContextTool`) never creates users or threads itself -- it assumes they already exist.

### ZepContextTool

A `BaseTool` subclass that hooks into ADK's `process_llm_request()` lifecycle method (the same pattern ADK's own `PreloadMemoryTool` uses). On every LLM turn it:

1. **Extracts** the user's latest message from the invocation context.
2. **Resolves** the user's Zep identity from session state.
3. **Persists** the message to Zep -- via `thread.add_messages(return_context=True)` in a single API call by default, or in parallel with a custom `context_builder` for advanced use cases. Over-limit message content (> 4096 chars) is truncated before persisting rather than dropped.
4. **Injects** the returned context (facts, relationships, prior knowledge) into the LLM's system instructions, wrapped by a configurable `context_template` (default: `DEFAULT_CONTEXT_TEMPLATE`, using `<ZEP_CONTEXT>` tags).

The tool is never called by the model directly; it modifies the outgoing LLM request before it is sent. If persistence fails because the user or the thread does not exist, a warning naming `create_user`/`create_thread` is logged and the turn continues without Zep memory.

### create_after_model_callback

A factory function that returns an `after_model_callback` for persisting assistant responses to Zep. This ensures both sides of the conversation are stored in Zep's memory. The callback also resolves the thread UUID from session state at runtime.

Both `ZepContextTool` and `create_after_model_callback` include per-thread message deduplication to handle ADK's tool-use cycles, where the framework may call hooks multiple times per turn.

## ADK-native memory service

`ZepMemoryService` implements ADK's own `BaseMemoryService` extension point, so Zep can back ADK's built-in `load_memory`/`preload_memory` tools. Register it on the `Runner`:

```python
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.tools import load_memory
from zep_cloud.client import AsyncZep
from zep_adk import ZepMemoryService

zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

agent = Agent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="You are a helpful assistant. Use load_memory to recall prior context when relevant.",
    tools=[load_memory],
)

runner = Runner(
    agent=agent,
    app_name="my_app",
    session_service=session_service,
    memory_service=ZepMemoryService(zep=zep, scope="edges"),
)
```

When the model calls `load_memory`, ADK invokes `ZepMemoryService.search_memory(app_name=..., user_id=..., query=...)`, where `user_id` holds the Zep user UUID. The service resolves the graph UUID of that user and runs the v4 search method for the configured scope (for example `zep.graph.search_edges(graph_uuid, query=query, limit=limit)`) and maps each result into a `MemoryEntry` (the same result shapes -- edges, nodes, episodes, observations, thread_summaries, auto -- as `ZepGraphSearchTool`). A Zep failure is logged (as a warning, with lengths/counts only) and returns an empty result rather than raising into the agent.

`add_session_to_memory` is intentionally a no-op: Zep already ingests each turn live via `ZepContextTool` (or `thread.add_messages`) and `create_after_model_callback`, so re-ingesting the full session here would persist the same conversation into the graph twice.

**When to use this vs. `ZepContextTool`:** `ZepMemoryService` is ADK-native and model-opt-in -- the model decides, per turn, whether to call `load_memory`. `ZepContextTool` guarantees injection -- it runs on every LLM turn regardless of what the model decides. The two are complementary: keep `ZepContextTool` for always-on context, and add `ZepMemoryService` when you also want the model to be able to explicitly search for more via ADK's own memory tools, or when integrating with ADK code paths that expect a `memory_service` (e.g. evaluation harnesses).

## Adding Zep to an Existing Agent

If you already have an ADK agent serving all users, adding Zep memory requires only a few changes -- no restructuring needed:

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

2. **Provision the Zep user and thread out-of-band** in your app/onboarding code, before the first turn:

```python
from zep_adk import create_user, create_thread

user = await create_user(zep, user_id=name_id, first_name=first_name, last_name=last_name)
thread = await create_thread(zep, user_uuid=user.uuid_)
```

3. **Include the Zep UUIDs and the user's name in session state** when creating sessions (you're already creating sessions -- just add the keys):

```python
await session_service.create_session(
    app_name="my_app",
    user_id=user_id,
    session_id=session_id,
    state={
        **your_existing_state,
        "zep_user_uuid": user.uuid_,
        "zep_thread_uuid": thread.uuid_,
        "zep_graph_uuid": user.graph_uuid,
        "zep_first_name": first_name,
        "zep_last_name": last_name,
    },
)
```

4. That's it. No factory function, no per-session agent instances.

## Migrating to the Zep v4 SDK

This version targets `zep-cloud` 4.x only. There is no v3 compatibility layer.

- **Identifiers are UUIDs.** Replace the `zep_user_id` and `zep_thread_id` session-state keys with `zep_user_uuid` and `zep_thread_uuid`, and add `zep_graph_uuid` when you already hold it. `ContextInput` carries `user_uuid` and `thread_uuid`.
- **`ensure_user`/`ensure_thread` are replaced by `create_user`/`create_thread`.** The new helpers create a resource and return it, because Zep generates a new UUID on every create call:

  ```python
  from zep_adk import create_user, create_thread

  user = await create_user(
      zep, user_id=name_id, first_name=first_name, last_name=last_name, email=email
  )
  thread = await create_thread(zep, user_uuid=user.uuid_)
  ```

- **The `on_created` hook and the `UserSetupHook` type are removed.** Run one-time user setup after `create_user` returns:

  ```python
  user = await create_user(zep, user_id=name_id)
  await my_setup_hook(zep, user.uuid_)
  ```

- **A custom `ContextBuilder` uses the v4 search methods and UUIDs:**

  ```python
  # Before (v3)
  async def my_builder(ctx: ContextInput) -> str | None:
      results = await ctx.zep.graph.search(user_id=ctx.user_id, query=ctx.user_message, scope="edges")
      return "\n".join(e.fact for e in results.edges or [])


  # After (v4)
  async def my_builder(ctx: ContextInput) -> str | None:
      user = await ctx.zep.user.get(ctx.user_uuid)
      pager = await ctx.zep.graph.search_edges(user.graph_uuid, query=ctx.user_message)
      return "\n".join(e.fact for e in pager.items or [] if e.fact)
  ```

  `ContextInput` also exposes `tool_context` (ADK session state / invocation metadata) and `llm_request` (the outgoing model request), which the old signature had no room for.

  Error isolation is unchanged in spirit but now applies independently to both sides of the concurrent persist/build: if the builder raises, a warning is logged and injection is skipped, but persistence still completes and the turn is marked as persisted. If persistence raises, a warning is logged and the turn is **not** marked as persisted (so it can be retried), but a successful builder result may still be injected.

## Changes in 0.3.0

- **Injected context is wrapped via a configurable template.** Pass `context_template` to `ZepContextTool` to override the wrapper around retrieved context (default: `DEFAULT_CONTEXT_TEMPLATE`, which still uses `<ZEP_CONTEXT>` tags):

  ```python
  from zep_adk import ZepContextTool, DEFAULT_CONTEXT_TEMPLATE

  tool = ZepContextTool(
      zep_client=zep,
      context_template="Relevant memory:\n{context}",
  )
  ```

  The template is rendered via plain string replacement (`template.replace("{context}", context_text)`), never `str.format` -- so context text or templates containing `{`, `}`, or `%` are always safe to inject.

- **The default injected context wording changed in 0.3.0.** Pre-0.3.0 hardcoded:

  ```text
  The following context is retrieved from Zep's long-term memory service. It contains relevant facts, relationships, and prior knowledge about the user. Use it to inform your responses.
  ```

  `DEFAULT_CONTEXT_TEMPLATE` (0.3.0+) now reads:

  ```text
  The following context is retrieved from Zep, the agent's long-term memory. It contains relevant facts, entities, and prior knowledge about the user. Use it to inform your responses.
  ```

  The `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper is unchanged. If you depend on the exact previous wording, pass it explicitly as `context_template`. This wording is canonical across zep-adk's Python, Go, and TypeScript implementations.

## Features

- **Shared-agent architecture** -- one Agent definition serves all users
- **Session-state-driven identity** -- per-user configuration via ADK's standard mechanism
- **Single round-trip** -- persist messages and retrieve context in one API call
- **Explicit out-of-band provisioning** -- `create_user`/`create_thread` provision Zep resources before the first turn and return the server-generated UUIDs
- **Per-thread deduplication** -- prevents double-persistence during tool-use cycles
- **Graceful error handling** -- Zep API failures are logged but never crash the agent
- **Context injection** -- Zep's knowledge graph context is injected as system instructions
- **On-demand graph search** -- `ZepGraphSearchTool` lets the model actively search the knowledge graph
- **Configurable graph search** -- pin parameters at construction or let the model choose
- **ADK-native memory service** -- `ZepMemoryService` implements `BaseMemoryService` so Zep backs ADK's own `load_memory`/`preload_memory` tools

## Configuration

### Environment Variables

```bash
# Required
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

### Constructor Parameters

#### create_user

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `user_id` | `str` | No | `None` | A human-readable name for the user. It is not an address. Give one: the v4 API rejects `thread.add_messages` for a user without it. |
| `first_name` | `str` | No | `None` | User's first name |
| `last_name` | `str` | No | `None` | User's last name |
| `email` | `str` | No | `None` | User's email |

Returns the created `User`. Read `user.uuid_` and `user.graph_uuid` and store them. Raises on failures (auth, network, 5xx).

#### create_thread

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `user_uuid` | `str` | Yes | -- | The UUID of the Zep user that owns the thread |
| `thread_id` | `str` | No | `None` | A human-readable name for the thread. It is not an address. |

Returns the created `Thread`. Read `thread.uuid_` and store it. Raises on failures (auth, network, 5xx).

#### ZepContextTool

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `zep_client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `context_builder` | `ContextBuilder` | No | `None` | Custom async callable (receives a `ContextInput`) for context retrieval |
| `context_template` | `str` | No | `DEFAULT_CONTEXT_TEMPLATE` | Template wrapping retrieved context; must contain a literal `{context}` placeholder |
| `ignore_roles` | `list[str]` | No | `None` | Roles to exclude from graph ingestion |

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
| `graph_uuid` | `str` | No | `None` | Fixed graph UUID for shared-graph search |
| `name` | `str` | No | `"zep_graph_search"` | Tool name visible to the model |
| `description` | `str` | No | (default) | Tool description visible to the model |
| `search_filters` | `dict` | No | `None` | Zep search filters (constructor-only) |
| `bfs_origin_node_uuids` | `list[str]` | No | `None` | BFS seed node UUIDs (constructor-only) |
| `**pinned` | any | No | -- | Pin any search param: `scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid` |

Every search parameter is **tri-state**, set via `**pinned`:

| State | How to set it | Effect |
|-------|----------------|--------|
| **Pinned** | pass a concrete value, e.g. `scope="edges"` | Hidden from the model's tool schema. Always used, even if the model would have chosen differently. |
| **Hidden** | pass `None`, e.g. `mmr_lambda=None` | Hidden from the model's tool schema AND omitted from the search call entirely. Only optional parameters can be hidden this way. |
| **Exposed** (default) | omit the kwarg entirely | Included in the model's tool schema with the default below, so the model chooses a value per call. |

`scope` accepts:

| Scope | Result |
|-------|--------|
| `edges` (default) | facts and relationships |
| `nodes` | entities and their summaries |
| `episodes` | raw text data (unstructured text, messages, or JSON) |
| `observations` | derived memories |
| `thread_summaries` | incremental thread summaries |
| `auto` | Zep's own pre-assembled mix of results |

#### ZepMemoryService

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `zep` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `scope` | `str` | No | `"edges"` | Graph search scope used by `search_memory`. Same enum as `ZepGraphSearchTool`: `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` |
| `limit` | `int` | No | `None` | Maximum results per search. `None` omits the parameter so the Zep SDK applies its own default |

## Examples

See the [examples/](examples/) directory for complete working examples:

- **[basic_agent.py](examples/basic_agent.py)** -- Full example with fact seeding and memory recall using the shared-agent pattern

## Development

### Setup

```bash
git clone https://github.com/getzep/zep.git
cd integrations/adk/python
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

- Python 3.11+
- `zep-cloud==4.0.0a5` (pre-release)
- `google-adk>=1.19.0,<3`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Google ADK Documentation](https://google.github.io/adk-docs/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
