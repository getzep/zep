# Zep LiveKit Integration

Add persistent memory to your LiveKit voice agents with [Zep's](https://www.getzep.com) memory capabilities. This integration provides both conversational memory for user sessions and shared knowledge graphs for cross-session information storage.

## Quick Start

### Install Dependencies

```bash
pip install zep-livekit
```

### Environment Setup

Configure your environment with the required API keys and LiveKit connection details:

```bash
# Required API keys
export OPENAI_API_KEY="your-openai-api-key"
export ZEP_API_KEY="your-zep-cloud-api-key"

# LiveKit configuration
export LIVEKIT_URL="your-livekit-url"
export LIVEKIT_API_KEY="your-livekit-api-key" 
export LIVEKIT_API_SECRET="your-livekit-api-secret"
```

## Memory Architecture

Zep uses a unified temporal knowledge graph where all conversation data contributes to a single, dynamic graph structure. The LiveKit integration provides two complementary approaches to interact with this unified memory:

### Thread-Based Memory Access (ZepUserAgent)
- **Purpose**: Structured conversation history and contextual retrieval
- **Storage**: Messages stored in threads that automatically contribute to the user's unified graph
- **Retrieval**: Context blocks assembled with temporal information from the graph
- **Use Case**: Personal assistants, customer support, tutoring sessions

### Direct Graph Memory Access (ZepGraphAgent)  
- **Purpose**: Direct interaction with the knowledge graph for shared information
- **Storage**: Information stored directly as facts, entities, and relationships in the graph
- **Retrieval**: Semantic search across the entire temporal knowledge graph
- **Use Case**: Knowledge bases, collaborative assistants, information systems

Both approaches work with the same underlying temporal knowledge graph - threads automatically enrich the graph with entities, relationships, and facts, while direct graph access allows for explicit knowledge management.

## Thread-Based Memory Access

Using structured conversation threads that automatically contribute to your unified graph.

> **UUID addressing.** Zep v4 addresses every user, thread, and graph by a
> server-generated UUID. A `user_id` or a `thread_id` is a name, not an address. Create
> each resource one time, store the UUID in your own database, and give the stored UUID
> to the agent. The integration does not resolve a name at run time.
>
> **Per-session identity.** `user_uuid` and `thread_uuid` (and `graph_uuid` for
> `ZepGraphAgent`) are fixed constructor arguments. Construct one agent, and normally one
> `AgentSession`, for each user or call.

### Basic Setup

```python
import os
from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep
from zep_livekit import ZepUserAgent

async def entrypoint(ctx: agents.JobContext):
    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Create the user one time and keep the UUID. A production application reads
    # the stored UUID from its own database instead.
    user = await zep_client.user.create(first_name="Alice")
    user_uuid = user.uuid_

    # Create a thread for this call and keep the UUID
    thread = await zep_client.thread.create(user_uuid=user_uuid)
    thread_uuid = thread.uuid_

    # Connect to room
    await ctx.connect()
    
    # Create session with providers
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-5-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )
    
    # Create memory-enabled agent
    agent = ZepUserAgent(
        zep_client=zep_client,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        instructions="You are a helpful assistant with persistent memory."
    )
    
    # Start conversation with memory
    await session.start(agent=agent, room=ctx.room)
```

### Advanced Configuration

```python
# Enhanced user agent with message attribution
agent = ZepUserAgent(
    zep_client=zep_client,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    user_message_name="Alice",  # Name for user messages in Zep
    assistant_message_name="Assistant",  # Name for assistant messages
    instructions="You remember our previous conversations and preferences."
)
```

## Provisioning

Provision the Zep user and thread out of band, before the first turn. `create_user` and
`create_thread` create the resources and return the response objects. Read `uuid_` from
each response and store the value in your own database. The helpers raise on failure, so
a misconfiguration is visible during onboarding:

```python
from zep_livekit import create_thread, create_user

async def seed_new_user(zep_client, user_uuid: str) -> None:
    """Runs one time, directly after the user is created."""
    ...  # seed initial facts, set custom instructions, configure ontology, etc.

user = await create_user(
    zep_client,
    first_name="Alice",
    on_created=seed_new_user,
)
user_uuid = user.uuid_
graph_uuid = user.graph_uuid  # the personal graph of the user

thread = await create_thread(zep_client, user_uuid=user_uuid)
thread_uuid = thread.uuid_
```

`ZepUserAgent` takes the stored UUIDs. The agent does not create a resource on the hot
path and does not resolve a name at run time.

`ZepGraphAgent` does **not** accept `on_created`: it is scoped to a graph, not to a Zep
user, so there is no "user created" event for a hook. Passing it raises `TypeError`.

## Direct Graph Memory Access

For explicit control over what gets stored as facts, entities, and relationships in your unified graph:

### Basic Setup

```python
from zep_livekit import ZepGraphAgent

async def entrypoint(ctx: agents.JobContext):
    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
    
    # Create the knowledge graph one time and keep the UUID
    graph = await zep_client.graph.create(
        name="Company Knowledge Base",
        description="Shared knowledge across all conversations"
    )
    graph_uuid = graph.uuid_

    # Connect to room
    await ctx.connect()
    
    # Create session
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-5-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )
    
    # Create knowledge-enabled agent
    agent = ZepGraphAgent(
        zep_client=zep_client,
        graph_uuid=graph_uuid,
        user_name="Alice",     # Optional: for message attribution
        max_characters=4000,   # Size limit of the assembled context block
        instructions="""
            You have access to a shared knowledge graph. 
            Store important facts for future reference and 
            search your knowledge to answer questions accurately.
        """
    )
    
    await session.start(agent=agent, room=ctx.room)
```


## Custom Context Builders

By default, `ZepUserAgent` folds persistence and retrieval into a single
`thread.add_messages(return_context=True)` round-trip, and `ZepGraphAgent` calls
`graph.get_context`. Pass `context_builder` to replace either with custom logic, for
example a filtered graph search, a different graph, or a multi-source context assembly:

```python
from zep_livekit import ContextInput

async def my_builder(ctx: ContextInput) -> str | None:
    page = await ctx.zep.graph.search_edges(
        graph_uuid,
        query=ctx.user_message,
    )
    if not page.items:
        return None
    return "\n".join(edge.fact for edge in page.items if edge.fact)

agent = ZepUserAgent(
    zep_client=zep_client,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    context_builder=my_builder,
)
```

When `context_builder` is set, message persistence and context building run
**concurrently** for lower latency, with per-side failure isolation: a builder error is
logged and skips injection for that turn but does not stop persistence; a persistence
error is logged but a successful builder result is still injected.

`ZepGraphAgent` takes the analogous `context_builder` typed as `GraphContextBuilder`,
receiving a `GraphContextInput` (`zep`, `graph_uuid`, `user_message`, `session`) in place
of `ContextInput`. On `ZepGraphAgent` the builder fully replaces the default
`graph.get_context` call. Graph message persistence already happens independently,
earlier in the turn.

### Context template

Both agents wrap injected context in `DEFAULT_CONTEXT_TEMPLATE` before adding it as a
system message. Override with `context_template` -- it must contain a literal `{context}`
placeholder, substituted via plain string replacement (never `str.format`, so context text
containing `{`, `}`, or `%` is always safe to inject):

```python
agent = ZepUserAgent(
    zep_client=zep_client,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    context_template="Known facts about the user:\n{context}",
)
```

## Graph Search Tool

In addition to the context injected automatically every turn, register a model-callable
tool that lets the agent search a Zep graph on demand:

```python
from zep_livekit import create_graph_search_tool

# Search the personal graph of a user. Read `graph_uuid` from the user object.
search_tool = create_graph_search_tool(zep_client, graph_uuid=user.graph_uuid)

# ...or a shared standalone graph.
search_tool = create_graph_search_tool(zep_client, graph_uuid=graph_uuid)

agent = ZepUserAgent(
    zep_client=zep_client,
    user_uuid=user_uuid,
    thread_uuid=thread_uuid,
    tools=[search_tool],
    instructions="...",
)
```

The tool exposes `scope` (`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`,
`auto`), `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid` to the model by default.
Use `pinned_params` to fix a parameter to a constant value (hidden from the model, always
sent), or `hidden_params` to hide a parameter without pinning it (Zep's own default
applies):

```python
search_tool = create_graph_search_tool(
    zep_client,
    graph_uuid=graph_uuid,
    pinned_params={"scope": "edges", "limit": 5},
    hidden_params={"center_node_uuid"},
)
```

`search_filters` and `bfs_origin_node_uuids` are always constructor-only. Zep failures are
caught and returned as an error string to the model -- the tool never raises into the
voice session.

## Querying Your Unified Graph

### Thread-Based Context Retrieval

```python
# Get conversation context assembled from the unified graph
memory_result = await zep_client.thread.get_context(thread_uuid)

if memory_result and memory_result.context:
    print(f"Context from unified graph: {memory_result.context}")
```

### Direct Graph Search

```python
# Search directly across the temporal knowledge graph. v4 has one method for each
# scope, and each method returns a pager. Read `.items`, or iterate the pager.
edges = await zep_client.graph.search_edges(
    graph_uuid,
    query="Python programming best practices",
    limit=10,
)
for edge in edges.items or []:
    print(edge.fact)

# Or let Zep assemble a prompt-ready context block from the graph
response = await zep_client.graph.get_context(
    graph_uuid,
    query="Python programming best practices",
)
print(response.context)
```

Ingestion in Zep is asynchronous. A message or an episode that you add is not
immediately retrievable. Give Zep time to process the data, or poll
`graph.episode.list` until each episode reports `processed`.

## Agent Comparison

| Agent Type | Best For | Memory Access Method | Use Cases |
|------------|----------|-------------------|-----------|
| **ZepUserAgent** | Personal assistants | Thread-based access to unified graph | Conversation continuity, customer support, tutoring |
| **ZepGraphAgent** | Knowledge systems | Direct graph access to unified graph | Shared information, collaborative assistants, knowledge bases |

### When to Use Each

**Use ZepUserAgent when:**
- Building personal assistants with structured conversation flow
- Need conversation history and context retrieval across sessions
- Want automatic thread-to-graph ingestion without manual management
- Prefer working with conversation-based memory access patterns

**Use ZepGraphAgent when:**  
- Building knowledge management systems with explicit fact storage
- Need direct semantic search across the temporal knowledge graph
- Want to manually control what information gets stored as facts
- Building systems where information should be immediately searchable across entities

## Complete Examples

### Personal Assistant
```bash
# examples/voice_assistant.py
python examples/voice_assistant.py
```

### Knowledge Assistant  
```bash
# examples/graph_voice_assistant.py
python examples/graph_voice_assistant.py
```

## API Reference

### ZepUserAgent

```python
class ZepUserAgent(agents.Agent):
    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_uuid: str,
        thread_uuid: str,
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any  # All LiveKit Agent parameters
    )
```

### ZepGraphAgent

```python
class ZepGraphAgent(agents.Agent):
    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        graph_uuid: str,
        user_name: str | None = None,
        search_filters: SearchFilters | None = None,
        max_characters: int | None = None,
        context_builder: GraphContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any  # All LiveKit Agent parameters
    )
```

### Provisioning

```python
async def create_user(
    client: AsyncZep,
    *,
    user_id: str | None = None,      # a name for the user, not an address
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> User: ...  # read `uuid_` and `graph_uuid` from the response

async def create_thread(
    client: AsyncZep, *, user_uuid: str, thread_id: str | None = None
) -> Thread: ...  # read `uuid_` from the response
```

### create_graph_search_tool

```python
def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    graph_uuid: str,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
) -> RawFunctionTool: ...
```


## Development

### Setup Development Environment

```bash
git clone https://github.com/getzep/zep
cd zep/integrations/livekit/python
make install
```

### Development Workflow

```bash
make format      # Format code with ruff
make lint        # Run linting checks  
make type-check  # Run MyPy type checking
make test        # Run test suite
make pre-commit  # Full pre-commit workflow
make ci          # Strict CI-style checks
```

## Support

### Zep Resources
- 📖 [Zep Documentation](https://help.getzep.com)
- 💬 [Zep Discord Community](https://discord.gg/W8xaHrqWVc)  
- 🐛 [GitHub Issues](https://github.com/getzep/zep/issues)
- 📧 [Email Support](mailto:support@getzep.com)

### LiveKit Resources  
- 📖 [LiveKit Documentation](https://docs.livekit.io)

---

Built with ❤️ by the [Zep](https://www.getzep.com) team.