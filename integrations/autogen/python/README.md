# Zep AutoGen Integration

A comprehensive integration package that enables [Zep](https://getzep.com) to work seamlessly with [Microsoft AutoGen](https://github.com/microsoft/autogen) agents, providing persistent conversation memory, knowledge graphs, and intelligent tool usage.

## Installation

```bash
pip install zep-autogen
```

## Quick Start

### Basic Memory Integration

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_autogen import ZepUserMemory
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient


async def main():
    # Initialize Zep client
    zep_client = AsyncZep(api_key="your-zep-api-key")

    # Create Zep memory for your agent
    memory = ZepUserMemory(
        client=zep_client,
        user_id="user_123",
        thread_id="conversation_456"
    )

    # Create AutoGen agent with Zep memory
    agent = AssistantAgent(
        name="MemoryAwareAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        memory=[memory]  # Add Zep memory to the agent
    )

    # Your agent now has persistent memory across conversations!
    response = await agent.run(task="What's my name again?")
    print(response.messages[-1].content)


asyncio.run(main())
```

### Tool-Equipped Agents

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_autogen import create_search_graph_tool, create_add_graph_data_tool
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient


async def main():
    # Initialize Zep client
    zep_client = AsyncZep(api_key="your-zep-api-key")
    
    # Create tools bound to your graph
    search_tool = create_search_graph_tool(zep_client, graph_id="my_knowledge_base")
    add_tool = create_add_graph_data_tool(zep_client, graph_id="my_knowledge_base")

    # Create agent with tools and reflection
    agent = AssistantAgent(
        name="KnowledgeAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-5-mini"),
        tools=[search_tool, add_tool],
        system_message="You can search and add information to knowledge bases.",
        reflect_on_tool_use=True,  # Enables natural language responses
    )

    # Agent can autonomously use tools and provide natural responses
    await Console(agent.run_stream(task="Add this fact: Python is great for AI development"))
    await Console(agent.run_stream(task="What do you know about Python?"))


asyncio.run(main())
```

## Features

- **Persistent Memory**: Conversations and knowledge persist across agent sessions
- **Graph Memory**: Store and retrieve structured knowledge using Zep's graph capabilities  
- **Tool Integration**: Pre-built AutoGen tools for search and data operations
- **Intelligent Retrieval**: Zep's memory automatically provides relevant context
- **AutoGen Compatible**: Seamlessly integrates with AutoGen's memory and tool interfaces
- **Natural Language Tool Use**: Agents process tool results and provide conversational responses
- **Async Support**: Full async/await support for modern applications
- **Type Safety**: Fully typed with comprehensive type hints

## The Zep memory loop, precisely

`ZepUserMemory` implements AutoGen's native `Memory` interface, which splits the memory loop
into two hooks with **different automaticity**:

- **Context INJECTION is automatic.** AutoGen calls `update_context()` before every model
  call. `ZepUserMemory` retrieves a Context Block from Zep (or runs your `context_builder`)
  and adds it to the model context as a system message. You never call this yourself.
- **PERSISTENCE is NOT automatic.** AutoGen never calls `add()` for you -- your application
  must call it explicitly, typically once per user turn and once per assistant turn. This is
  AutoGen's design (the `Memory` protocol has no "after model responds" hook), not a
  limitation of this integration.

The canonical wiring for a full turn:

```python
from autogen_core.memory import MemoryContent, MemoryMimeType

memory = ZepUserMemory(client=zep_client, user_id="user_123", thread_id="conversation_456")
agent = AssistantAgent(name="Assistant", model_client=model_client, memory=[memory])

user_text = "What's the weather like for my trip?"

# 1. Persist the user's turn (NOT automatic).
await memory.add(
    MemoryContent(
        content=user_text,
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "user"},
    )
)

# 2. Run the agent. update_context() fires automatically before the model call
#    and injects Zep's Context Block as a system message.
response = await agent.run(task=user_text)

# 3. Persist the assistant's reply (NOT automatic).
await memory.add(
    MemoryContent(
        content=str(response.messages[-1].content),
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "assistant"},
    )
)
```

If you skip step 1/3, the agent still gets Zep's *existing* Context Block on every turn (via
`update_context()`), but that turn's messages are never written to Zep and will not be
recallable later.

### Lazy provisioning

The Zep user and thread are created lazily, on first use, by whichever of `add()` /
`update_context()` runs first -- you do not have to pre-create them. Creation is idempotent
and cached per `ZepUserMemory` instance. Pass `first_name`, `last_name`, and `email` to the
constructor so Zep can anchor the user's identity node in the graph, and `on_created` to run
one-time setup (ontology, custom instructions) the first time the user is actually created:

```python
async def setup_new_user(zep, user_id: str) -> None:
    await zep.user.add_ontology(user_id=user_id, ...)  # example one-time setup

memory = ZepUserMemory(
    client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_new_user,
)
```

This lazy path is hot-path-wrapped: a genuine provisioning failure (or an `on_created` hook
failure) is logged and swallowed -- it never raises into `add()`/`update_context()`. If you
want provisioning failures to surface loudly (e.g. during account onboarding, before the
first turn), call `ensure_user`/`ensure_thread` directly, out-of-band:

```python
from zep_autogen import ensure_user, ensure_thread

await ensure_user(zep_client, user_id="user_123", first_name="Jane", on_created=setup_new_user)
await ensure_thread(zep_client, thread_id="conversation_456", user_id="user_123")
```

`ZepGraphMemory` has no equivalent `on_created` hook: it is scoped to a standalone
`graph_id` (e.g. a shared knowledge base), not a Zep user, so there is no per-user setup step
to run. Create the graph out-of-band via `client.graph.create(graph_id=...)` if needed.

### Custom context retrieval with `context_builder`

By default, `update_context()` retrieves context via `thread.get_user_context(...)`. Pass
`context_builder` to replace this with custom logic -- e.g. a filtered graph search, or a
different graph entirely:

```python
from zep_autogen.memory import ContextInput

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

memory = ZepUserMemory(
    client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    context_builder=my_builder,
)
```

`ctx.user_message` is the last user-role message's text from the AutoGen `model_context`
passed to `update_context()` (`""` if none is found). If the builder raises, a warning is
logged and context injection is skipped for that turn -- `update_context()` never raises.
Unlike the ADK/Microsoft Agent Framework/Pydantic AI ports of this pattern, the builder here
never runs concurrently with message persistence: AutoGen's `Memory` protocol calls
`update_context()` (injection) and `add()` (persistence) as two separate, caller-controlled
steps, so there is nothing to run the builder alongside.

### Customizing the injected context template

The retrieved context (whether from the default retrieval or a `context_builder`) is wrapped
in `context_template` before being added to the model context as a system message. Override
it with your own wording, as long as it contains a literal `{context}` placeholder:

```python
memory = ZepUserMemory(
    client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    context_template="Relevant background:\n{context}",
)
```

The template is rendered via `template.replace("{context}", context_text)` -- never
`str.format` -- so context text containing `{`, `}`, or `%` is always safe to inject.

## Configuration

### Environment Variables

```bash
# Required: Your Zep Cloud API key
export ZEP_API_KEY="your-zep-api-key"
```

### Memory Classes

#### ZepUserMemory
For conversational memory that persists across threads:

- `client` (AsyncZep): Your Zep client instance
- `user_id` (str): Unique identifier for the user
- `thread_id` (str, optional): Thread/conversation identifier
- `first_name`, `last_name`, `email` (str, optional): Passed to `user.add` during lazy
  provisioning; helps Zep anchor the user's identity node in the graph
- `on_created` (optional): Async hook run exactly once, only when the user is newly created
  during lazy provisioning
- `context_builder` (optional): Async callable replacing the default context retrieval in
  `update_context()` -- see "Custom context retrieval" below
- `context_template` (str, optional): Template wrapping injected context (default:
  `DEFAULT_CONTEXT_TEMPLATE`)

#### ZepGraphMemory  
For knowledge graph storage and retrieval:

- `client` (AsyncZep): Your Zep client instance
- `graph_id` (str): Identifier for the knowledge graph

### Tool Functions

#### create_search_graph_tool
Creates a search tool bound to a graph or user:

- `client` (AsyncZep): Your Zep client instance
- `graph_id` (str, optional): Graph to search (for general knowledge graphs)
- `user_id` (str, optional): User to search (for user knowledge graphs)
- `pinned_params` (dict, optional): Fix a `graph.search` parameter to a constant value,
  hiding it from the model's tool schema.
- `hidden_params` (set, optional): Hide a parameter from the model's tool schema without
  pinning it -- Zep's own server-side default applies.
- `search_filters` (dict, optional), `bfs_origin_node_uuids` (list, optional):
  constructor-only, never exposed to the model.
- `scope`, `limit` (optional): Legacy back-compat aliases that pin (and hide) the
  corresponding parameter -- equivalent to `pinned_params={"scope": ..., "limit": ...}`.

**Pin-or-expose schema.** By default, the tool exposes five `graph.search` parameters to the
model, each with a documented default:

| Parameter | Default | Description |
|---|---|---|
| `scope` | `"edges"` | One of `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` |
| `reranker` | `"rrf"` | One of `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder` |
| `limit` | `10` | Maximum number of results |
| `mmr_lambda` | `None` | Diversity/relevance balance (only used when `reranker="mmr"`) |
| `center_node_uuid` | `None` | Center node for `reranker="node_distance"` |

```python
# Expose everything to the model (default)
tool = create_search_graph_tool(zep_client, user_id="user_123")

# Pin scope and limit -- hidden from the model, always sent as given
tool = create_search_graph_tool(
    zep_client,
    user_id="user_123",
    pinned_params={"scope": "nodes", "limit": 5},
)

# Hide mmr_lambda from the schema without pinning it -- Zep's own default applies
tool = create_search_graph_tool(
    zep_client, user_id="user_123", hidden_params={"mmr_lambda"}
)
```

> AutoGen's `FunctionTool` derives its JSON schema strictly from the wrapped Python
> function's typed signature -- there is no raw-JSON-schema constructor argument like some
> other frameworks provide. `create_search_graph_tool` implements pin-or-expose by
> dynamically building that signature: exposed parameters become real, typed parameters of
> the function AutoGen introspects, while pinned/hidden parameters are never part of the
> function's signature at all.

#### create_add_graph_data_tool
Creates a data addition tool bound to a graph or user:

- `client` (AsyncZep): Your Zep client instance
- `graph_id` (str, optional): Graph to add data to
- `user_id` (str, optional): User to add data for

### Size limits

Zep rejects over-long payloads with an HTTP 400. This integration truncates instead of
letting the call fail, logging only the before/after lengths (never the content):

- Thread messages (`ZepUserMemory.add`, message type): truncated to 4,000 characters
  (Zep's hard limit is 4,096).
- Graph data (`ZepGraphMemory.add`, `create_add_graph_data_tool`): truncated to 9,900
  characters, a safety margin under Zep's `graph.add` ceiling.

## Examples

### Memory Integration

- **[Basic Memory](examples/autogen_basic.py)**: User memory with conversation persistence
- **[Graph Memory](examples/autogen_graph.py)**: Knowledge graphs with ontology definitions

### Tool Integration

- **[Search Tools](examples/autogen_tools_search.py)**: Agents with search-only capabilities  
- **[Full Tools](examples/autogen_tools_full.py)**: Agents that can both search and add data

### Multi-Agent with Shared Memory

```python
# Multiple agents can share the same memory context
shared_memory = ZepUserMemory(
    client=zep_client,
    user_id="team_project", 
    thread_id="brainstorm_session"
)

researcher = AssistantAgent(
    name="Researcher",
    model_client=model_client,
    memory=[shared_memory]
)

writer = AssistantAgent(
    name="Writer",
    model_client=model_client, 
    memory=[shared_memory]
)
```

## Advanced Usage

### Graph Memory with Ontology

Define structured entities for better knowledge organization:

```python
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from pydantic import Field

class ProgrammingLanguage(EntityModel):
    paradigm: EntityText = Field(description="programming paradigm")  
    use_case: EntityText = Field(description="primary use cases")

# Set graph ontology
await zep_client.graph.set_ontology(
    entities={"ProgrammingLanguage": ProgrammingLanguage},
    edges={}
)

# Use graph memory with ontology
memory = ZepGraphMemory(
    client=zep_client,
    graph_id="tech_knowledge",
    search_filters=SearchFilters(
        node_labels=["ProgrammingLanguage"],
    ),
)
```

### Tool Usage
```python

agent = AssistantAgent(
    name="Assistant",
    model_client=model_client,
    tools=[search_tool],
    reflect_on_tool_use=True, 
)

# Use streaming console for tool visualization
await Console(agent.run_stream(task="Search for Python information"))
```

## Development

### Setup
```bash
# Install development dependencies
make install

# Run pre-commit checks (format, lint, type-check, test)
make pre-commit
```

### Available Commands
- `make format` - Format code with ruff
- `make lint` - Run linting checks
- `make type-check` - Run type checking with mypy  
- `make test` - Run tests
- `make all` - Run all checks

## Requirements

- Python 3.11+
- `zep-cloud>=3.23.0`
- `autogen-agentchat>=0.7.0`
- `autogen-ext[azure,openai]>=0.7.0`

## License

MIT License - see [LICENSE](LICENSE) for details.

## Support

- [Zep Documentation](https://help.getzep.com)
- [AutoGen Documentation](https://microsoft.github.io/autogen/stable/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.