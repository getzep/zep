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

> **Per-session identity.** `user_id`/`thread_id` (and `graph_id` for `ZepGraphAgent`) are
> fixed constructor arguments, resolved once and not re-resolved per turn. This is
> idiomatic for voice: construct one agent (and typically one `AgentSession`) per
> user/call rather than sharing a single instance across users.

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
    
    # Create user and thread
    user_id = "user_123"
    thread_id = f"conversation_{user_id}"
    
    try:
        await zep_client.user.get(user_id=user_id)
    except:
        await zep_client.user.add(user_id=user_id, first_name="Alice")
    
    await zep_client.thread.create(thread_id=thread_id, user_id=user_id)
    
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
        user_id=user_id,
        thread_id=thread_id,
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
    user_id="user_123",
    thread_id="conversation_456",
    user_message_name="Alice",  # Name for user messages in Zep
    assistant_message_name="Assistant",  # Name for assistant messages
    instructions="You remember our previous conversations and preferences."
)
```

## Provisioning

Prefer explicit, out-of-band provisioning with `ensure_user`/`ensure_thread` before the
first turn -- e.g. during account/session onboarding. Both are idempotent
(create-then-catch-conflict) and return whether the resource was newly created; genuine
failures (auth, network, 5xx) always raise:

```python
from zep_livekit import ensure_thread, ensure_user

async def seed_new_user(zep_client, user_id: str) -> None:
    """Runs exactly once, right after the user is first created."""
    ...  # seed initial facts, set custom instructions, configure ontology, etc.

created = await ensure_user(
    zep_client,
    user_id="user_123",
    first_name="Alice",
    on_created=seed_new_user,  # fires only for a genuinely new user
)
await ensure_thread(zep_client, thread_id="conversation_456", user_id="user_123")
```

`ZepUserAgent` also accepts `first_name`/`last_name`/`email`/`on_created` directly and
lazily calls the same helpers on the first turn (cached per agent instance) -- convenient
for prototyping, but this lazy path always logs and swallows failures rather than raising
into the voice session. Prefer the explicit out-of-band call above when you need
provisioning failures to surface loudly.

`ZepGraphAgent` does **not** accept `on_created`: it is scoped to a standalone `graph_id`,
not a Zep user, so there is no "user created" event to hook into. Passing it raises
`TypeError`.

## Direct Graph Memory Access

For explicit control over what gets stored as facts, entities, and relationships in your unified graph:

### Basic Setup

```python
from zep_livekit import ZepGraphAgent

async def entrypoint(ctx: agents.JobContext):
    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
    
    # Create or get knowledge graph
    graph_id = "company_knowledge_base"
    try:
        await zep_client.graph.get(graph_id)
    except:
        await zep_client.graph.create(
            graph_id=graph_id,
            name="Company Knowledge Base",
            description="Shared knowledge across all conversations"
        )
    
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
        graph_id=graph_id,
        user_name="Alice",  # Optional: for message attribution
        facts_limit=15,     # Max facts to retrieve
        entity_limit=5,     # Max entities to retrieve
        episode_limit=3,    # Max episodes to retrieve
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
`thread.add_messages(return_context=True)` round-trip, and `ZepGraphAgent` runs a hybrid
search across edges, nodes, and episodes. Pass `context_builder` to replace either with
custom logic -- e.g. a filtered graph search, a different graph entirely, or a
multi-source context assembly:

```python
from zep_livekit import ContextInput

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

agent = ZepUserAgent(
    zep_client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    context_builder=my_builder,
)
```

When `context_builder` is set, message persistence and context building run
**concurrently** for lower latency, with per-side failure isolation: a builder error is
logged and skips injection for that turn but does not stop persistence; a persistence
error is logged but a successful builder result is still injected.

`ZepGraphAgent` takes the analogous `context_builder` typed as `GraphContextBuilder`,
receiving a `GraphContextInput` (`zep`, `graph_id`, `user_message`, `session`) in place of
`ContextInput`. Unlike `ZepUserAgent`, setting it fully replaces the default hybrid search
rather than running concurrently with anything (graph message persistence already happens
independently, earlier in the turn).

### Context template

Both agents wrap injected context in `DEFAULT_CONTEXT_TEMPLATE` before adding it as a
system message. Override with `context_template` -- it must contain a literal `{context}`
placeholder, substituted via plain string replacement (never `str.format`, so context text
containing `{`, `}`, or `%` is always safe to inject):

```python
agent = ZepUserAgent(
    zep_client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    context_template="Known facts about the user:\n{context}",
)
```

## Graph Search Tool

In addition to the context injected automatically every turn, register a model-callable
tool that lets the agent search a Zep graph on demand:

```python
from zep_livekit import create_graph_search_tool

# Search a user's personal graph...
search_tool = create_graph_search_tool(zep_client, user_id="user_123")

# ...or a shared standalone graph. Exactly one of graph_id/user_id is required.
search_tool = create_graph_search_tool(zep_client, graph_id="company_knowledge_base")

agent = ZepUserAgent(
    zep_client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
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
    user_id="user_123",
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
memory_result = await zep_client.thread.get_user_context(
    thread_id="conversation_123",
)

if memory_result and memory_result.context:
    print(f"Context from unified graph: {memory_result.context}")
```

### Direct Graph Search

```python
# Search directly across the temporal knowledge graph
search_results = await zep_client.graph.search(
    graph_id="company_knowledge_base",
    query="Python programming best practices",
    limit=10,
    scope="edges"  # facts, or "nodes" (entities), "episodes"
)

# Use Zep's utility to compose context
from zep_cloud.graph.utils import compose_context_string
context = compose_context_string(
    search_results.edges,
    search_results.nodes, 
    search_results.episodes
)
```

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
        user_id: str,
        thread_id: str,
        context_mode: Literal["basic", "summary"] | None = None,  # Deprecated, ignored
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        on_created: UserSetupHook | None = None,
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
        graph_id: str,
        user_name: str | None = None,
        facts_limit: int = 15,
        entity_limit: int = 5, 
        episode_limit: int = 2,
        search_filters: SearchFilters | None = None,
        reranker: Reranker | None = "rrf",
        context_builder: GraphContextBuilder | None = None,  # cannot combine with on_created
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any  # All LiveKit Agent parameters
    )
```

### Provisioning

```python
async def ensure_user(
    client: AsyncZep,
    *,
    user_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> bool: ...  # True iff newly created

async def ensure_thread(client: AsyncZep, *, thread_id: str, user_id: str) -> bool: ...
```

### create_graph_search_tool

```python
def create_graph_search_tool(
    zep_client: AsyncZep,
    *,
    graph_id: str | None = None,   # exactly one of graph_id/user_id required
    user_id: str | None = None,
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