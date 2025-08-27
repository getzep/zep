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

Using structured conversation threads that automatically contribute to your unified graph:

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
        llm=openai.LLM(model="gpt-4o-mini"),
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
    context_mode="summary",  # or "basic"
    user_message_name="Alice",  # Name for user messages in Zep
    assistant_message_name="Assistant",  # Name for assistant messages
    instructions="You remember our previous conversations and preferences."
)
```

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
        llm=openai.LLM(model="gpt-4o-mini"),
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


## Querying Your Unified Graph

### Thread-Based Context Retrieval

```python
# Get conversation context assembled from the unified graph
memory_result = await zep_client.thread.get_user_context(
    thread_id="conversation_123",
    mode="basic"  # or "summary"
)

if memory_result and memory_result.context:
    print(f"Context from unified graph: {memory_result.context}")
```

### Direct Graph Search

```python
# Search directly across the temporal knowledge graph
search_results = await zep_client.graph.search(
    graph_id="user_graph",
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
        context_mode: Literal["basic", "summary"] = "basic",
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
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
        **kwargs: Any  # All LiveKit Agent parameters
    )
```


## Development

### Setup Development Environment

```bash
git clone https://github.com/getzep/zep
cd zep/integrations/python/zep_livekit
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
- 🏗️ [LiveKit Platform](https://cloud.livekit.io)
- 👥 [LiveKit Community](https://livekit.io/community)
- 📚 [LiveKit Agents Guide](https://docs.livekit.io/agents)

---

Built with ❤️ by the [Zep](https://www.getzep.com) team.