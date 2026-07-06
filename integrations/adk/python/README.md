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
| explicit provisioning + created signal | `ensure_user`/`ensure_thread` | `ensureUser`/`ensureThread` | `EnsureUser`/`EnsureThread` |
| custom context block | `context_builder` | `contextBuilder` | `WithContextBuilder` |
| injection template | `context_template` | `contextTemplate` | `WithContextTemplate` |
| model-callable graph search (pin-or-expose, 6 scopes) | `ZepGraphSearchTool` | `ZepGraphSearchTool` | `NewGraphSearchTool` |
| ADK-native memory service | `ZepMemoryService` | `ZepMemoryService` | `NewMemoryService` |

Note: Go intentionally has no tool-based injection (callbacks are the Go-ADK-idiomatic hook).

Note: Go has no `on_created` hook -- use the `created` bool returned by `EnsureUser`/`EnsureThread` instead. Go's `EnsureUser` takes positional `firstName`, `lastName`, `email` strings (pass `""` to omit).

## Quick Start

Define one agent, shared across all users. Per-user identity is passed via ADK session state.

```python
import os
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from zep_cloud.client import AsyncZep
from zep_adk import ZepContextTool, create_after_model_callback, ensure_user, ensure_thread

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
# e.g. during account/session onboarding in your app.
await ensure_user(
    zep,
    user_id="user_123",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",  # optional
)
await ensure_thread(zep, thread_id="session_abc", user_id="user_123")

# Per-user session: user_id → Zep user, session_id → Zep thread
await session_service.create_session(
    app_name="my_app",
    user_id="user_123",          # automatically used as Zep user ID
    session_id="session_abc",    # automatically used as Zep thread ID
    state={
        "zep_first_name": "Jane",
        "zep_last_name": "Smith",
    },
)
```

## Session State Keys

Identity is resolved at runtime from ADK session state and session metadata. The ADK `user_id` is used as the Zep user ID and the `session_id` is used as the Zep thread ID -- both automatically, no state keys needed.

Set these optional keys when creating a session to attribute messages to the user:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `zep_first_name` | Recommended | `"Anonymous"` | User's first name. Attached as the author name on persisted messages so Zep can anchor them to the user's identity node in the knowledge graph. |
| `zep_last_name` | Optional | `"User"` | User's last name. |
| `zep_user_id` | Optional | ADK `user_id` | Override the Zep user ID if it differs from the ADK user ID. |
| `zep_thread_id` | Optional | ADK `session_id` | Override the Zep thread ID if it differs from the ADK session ID. |

The user's name and email on the Zep user profile itself are set during
provisioning -- pass them to `ensure_user`, not session state.

## How It Works

The integration uses four components that work together to give your ADK agent persistent memory:

### ensure_user / ensure_thread

Explicit, idempotent, out-of-band provisioning helpers. Call these once -- during onboarding, account creation, or before the first turn of a new conversation -- **before** the agent runs. They are create-then-catch-conflict: each calls the Zep SDK's create method directly and returns `True` if the resource was newly created, `False` if it already existed. Genuine failures (auth, network, 5xx) raise, so misconfiguration is caught immediately rather than silently swallowed.

`ensure_user` accepts an optional `on_created` hook that runs exactly once, only for genuinely new users -- the place to configure per-user ontology, custom instructions, or summary instructions. If the hook raises, the exception propagates (the user was still created). Because the user now exists, retrying `ensure_user` will *not* re-run the hook -- keep the hook idempotent and re-run its logic directly to recover from a partial failure.

The ADK turn path (`ZepContextTool`) never creates users or threads itself -- it assumes they already exist.

### ZepContextTool

A `BaseTool` subclass that hooks into ADK's `process_llm_request()` lifecycle method (the same pattern ADK's own `PreloadMemoryTool` uses). On every LLM turn it:

1. **Extracts** the user's latest message from the invocation context.
2. **Resolves** the user's Zep identity from session state.
3. **Persists** the message to Zep -- via `thread.add_messages(return_context=True)` in a single API call by default, or in parallel with a custom `context_builder` for advanced use cases. Over-limit message content (> 4096 chars) is truncated before persisting rather than dropped.
4. **Injects** the returned context (facts, relationships, prior knowledge) into the LLM's system instructions, wrapped by a configurable `context_template` (default: `DEFAULT_CONTEXT_TEMPLATE`, using `<ZEP_CONTEXT>` tags).

The tool is never called by the model directly; it modifies the outgoing LLM request before it is sent. If persistence fails because the user/thread doesn't exist yet, a warning naming `ensure_user`/`ensure_thread` is logged and the turn continues without Zep memory.

### create_after_model_callback

A factory function that returns an `after_model_callback` for persisting assistant responses to Zep. This ensures both sides of the conversation are stored in Zep's memory. The callback also resolves the thread ID from session state at runtime.

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

When the model calls `load_memory`, ADK invokes `ZepMemoryService.search_memory(app_name=..., user_id=..., query=...)`, which runs `zep.graph.search(user_id=user_id, query=query, scope=scope, limit=limit)` against the calling user's graph and maps each result into a `MemoryEntry` (the same result shapes -- edges, nodes, episodes, observations, thread_summaries, auto -- as `ZepGraphSearchTool`). A Zep failure is logged (as a warning, with lengths/counts only) and returns an empty result rather than raising into the agent.

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
from zep_adk import ensure_user, ensure_thread

await ensure_user(zep, user_id=user_id, first_name=first_name, last_name=last_name)
await ensure_thread(zep, thread_id=session_id, user_id=user_id)
```

3. **Include the user's name in session state** when creating sessions (you're already creating sessions -- just add the keys):

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

4. That's it. No factory function, no per-session agent instances.

## Migrating from 0.2.x

Version 0.3.0 removes lazy, in-band Zep user/thread creation from the ADK turn path in favor of explicit, out-of-band provisioning:

- **Lazy creation is gone.** `ZepContextTool.process_llm_request()` no longer calls `user.add`/`thread.create`. If the user/thread don't exist yet, persistence fails with a logged warning (the turn continues without Zep memory) instead of silently creating them.
- **Call `ensure_user`/`ensure_thread` yourself**, once, before the first turn -- typically in your app's account or session onboarding code:

  ```python
  from zep_adk import ensure_user, ensure_thread

  await ensure_user(zep, user_id=user_id, first_name=first_name, last_name=last_name, email=email)
  await ensure_thread(zep, thread_id=thread_id, user_id=user_id)
  ```

- **`on_user_created` moved.** The `ZepContextTool(..., on_user_created=...)` constructor argument has been removed. Pass the hook to `ensure_user` instead:

  ```python
  # Before (0.2.x)
  ZepContextTool(zep_client=zep, on_user_created=my_setup_hook)

  # After (0.3.0+)
  await ensure_user(zep, user_id=user_id, on_created=my_setup_hook)
  ```

  The hook's signature (`async def hook(zep_client, user_id) -> None`) is unchanged, as is the "fires only for genuinely new users, exceptions propagate" behavior.

- **`ContextBuilder` now takes a single `ContextInput` argument** (breaking change). The old 4-positional-argument signature is gone in favor of a single frozen dataclass, so new fields can be added later without breaking existing builders:

  ```python
  # Before (0.2.x)
  async def my_builder(
      zep_client: AsyncZep,
      user_id: str,
      thread_id: str,
      user_message: str,
  ) -> str | None:
      results = await zep_client.graph.search(user_id=user_id, query=user_message, scope="edges")
      return "\n".join(e.fact for e in results.edges or [])

  # After (0.3.0+)
  from zep_adk import ContextInput

  async def my_builder(ctx: ContextInput) -> str | None:
      results = await ctx.zep.graph.search(user_id=ctx.user_id, query=ctx.user_message, scope="edges")
      return "\n".join(e.fact for e in results.edges or [])
  ```

  `ContextInput` also exposes `tool_context` (ADK session state / invocation metadata) and `llm_request` (the outgoing model request), which the old signature had no room for.

  Error isolation is unchanged in spirit but now applies independently to both sides of the concurrent persist/build: if the builder raises, a warning is logged and injection is skipped, but persistence still completes and the turn is marked as persisted. If persistence raises, a warning is logged and the turn is **not** marked as persisted (so it can be retried), but a successful builder result may still be injected.

- **Injected context is now wrapped via a configurable template.** Pass `context_template` to `ZepContextTool` to override the wrapper around retrieved context (default: `DEFAULT_CONTEXT_TEMPLATE`, which still uses `<ZEP_CONTEXT>` tags):

  ```python
  from zep_adk import ZepContextTool, DEFAULT_CONTEXT_TEMPLATE

  tool = ZepContextTool(
      zep_client=zep,
      context_template="Relevant memory:\n{context}",
  )
  ```

  The template is rendered via plain string replacement (`template.replace("{context}", context_text)`), never `str.format` -- so context text or templates containing `{`, `}`, or `%` are always safe to inject.

- **The default injected context wording changed.** Pre-0.3.0 hardcoded:

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
- **Explicit out-of-band provisioning** -- `ensure_user`/`ensure_thread` idempotently provision Zep resources before the first turn
- **Per-thread deduplication** -- prevents double-persistence during tool-use cycles
- **Graceful error handling** -- Zep API failures are logged but never crash the agent
- **Context injection** -- Zep's knowledge graph context is injected as system instructions
- **Per-user setup hook** -- `ensure_user(on_created=...)` callback for configuring ontology, instructions, and summaries on newly created users
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

#### ensure_user

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `user_id` | `str` | Yes | -- | The Zep user ID to create |
| `first_name` | `str` | No | `None` | User's first name |
| `last_name` | `str` | No | `None` | User's last name |
| `email` | `str` | No | `None` | User's email |
| `on_created` | `UserSetupHook` | No | `None` | Async callback awaited exactly once, only when the user is newly created. Use for per-user ontology, custom instructions, or user summary instructions. Exceptions propagate. |

Returns `True` if the user was newly created, `False` if it already existed. Raises on genuine failures (auth, network, 5xx).

#### ensure_thread

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `client` | `AsyncZep` | Yes | -- | Initialised Zep async client |
| `thread_id` | `str` | Yes | -- | The Zep thread ID to create |
| `user_id` | `str` | Yes | -- | The Zep user ID that owns the thread (must already exist) |

Returns `True` if the thread was newly created, `False` if it already existed. Raises on genuine failures (auth, network, 5xx).

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
| `graph_id` | `str` | No | `None` | Fixed graph ID for shared-graph search |
| `name` | `str` | No | `"zep_graph_search"` | Tool name visible to the model |
| `description` | `str` | No | (default) | Tool description visible to the model |
| `search_filters` | `dict` | No | `None` | Zep search filters (constructor-only) |
| `bfs_origin_node_uuids` | `list[str]` | No | `None` | BFS seed node UUIDs (constructor-only) |
| `**pinned` | any | No | -- | Pin any search param: `scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid` |

Every search parameter is **tri-state**, set via `**pinned`:

| State | How to set it | Effect |
|-------|----------------|--------|
| **Pinned** | pass a concrete value, e.g. `scope="edges"` | Hidden from the model's tool schema. Always used, even if the model would have chosen differently. |
| **Hidden** | pass `None`, e.g. `mmr_lambda=None` | Hidden from the model's tool schema AND omitted from the `graph.search` call entirely. Only optional parameters can be hidden this way. |
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
- `zep-cloud>=3.23.0`
- `google-adk>=1.19.0,<3`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Google ADK Documentation](https://google.github.io/adk-docs/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
