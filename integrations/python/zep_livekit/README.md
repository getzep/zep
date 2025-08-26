# Zep LiveKit Integration

A memory-enabled voice AI agent integration that combines [Zep's](https://www.getzep.com) persistent memory capabilities with [LiveKit's](https://livekit.io) realtime voice AI framework.

## Features

🧠 **Persistent Memory**: Remember conversations across sessions using Zep's graph-based memory
🎙️ **Voice AI**: Production-ready voice agents with LiveKit's realtime framework  
🤖 **OpenAI Integration**: Seamless STT, LLM, and TTS using OpenAI providers
👤 **User & Thread Isolation**: Separate memory spaces for different users and conversations
📊 **Graph Knowledge**: Extract entities and relationships from voice interactions
🔄 **Context Injection**: Automatically enhance conversations with relevant memories

## Quick Start

### Installation

```bash
pip install zep-livekit
```

### Basic Usage

```python
import asyncio
from zep_cloud.client import AsyncZep
from livekit.plugins import openai, silero
from livekit import agents
from zep_livekit import ZepMemoryAgent, ZepAgentSession

async def main():
    # Initialize Zep client
    zep_client = AsyncZep(api_key="your-zep-api-key")
    
    # Ensure user exists (create it if needed)
    user_id = "user123"
    try:
        await zep_client.user.get(user_id)
    except:
        await zep_client.user.add(user_id=user_id, metadata={})
    
    # Ensure thread exists (create it if needed)
    thread_id = "conversation456"
    try:
        await zep_client.thread.get(thread_id)
    except:
        await zep_client.thread.create(thread_id=thread_id, user_id=user_id)
    
    # Create memory-enabled agent
    agent = ZepMemoryAgent(
        zep_client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
        instructions="You are a helpful assistant with memory."
    )
    
    # Create Zep-enabled session that automatically captures conversations
    session = ZepAgentSession(
        zep_client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
        # Standard LiveKit providers
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )
    
    # Session automatically captures and stores conversations in Zep
    # See examples/voice_assistant.py for complete setup

asyncio.run(main())
```

### Environment Setup

Set the following environment variables:

```bash
# Required
export OPENAI_API_KEY="your-openai-api-key"
export ZEP_API_KEY="your-zep-api-key" 
export LIVEKIT_URL="wss://your-livekit-server.com"
export LIVEKIT_API_KEY="your-livekit-api-key"
export LIVEKIT_API_SECRET="your-livekit-api-secret"
```

## Complete Example

See `examples/voice_assistant.py` for a full working voice assistant:

```bash
# Run with default settings
python examples/voice_assistant.py

# Custom room and user
python examples/voice_assistant.py my-room user123

# Resume existing conversation
python examples/voice_assistant.py my-room user123 thread_abc123
```

## How Memory Works

### Dual Storage Strategy

The integration uses both **user-focused** and **graph-focused** memory:

1. **Thread Memory**: Stores conversation history in user threads
   - Maintains conversational context
   - Preserves message ordering and timing
   - Enables conversation resumption

2. **Graph Memory**: Extracts knowledge into user graphs  
   - Builds entity relationships
   - Enables semantic search
   - Creates long-term knowledge base

### Memory Lifecycle

1. **Storage** (`on_user_turn_completed`):
   - Save messages to thread history
   - Extract facts for graph storage
   - Build entity relationships

2. **Retrieval** (`update_chat_ctx`):
   - Search graph for relevant context
   - Get recent thread history
   - Inject memory into conversation

3. **Context Enhancement**:
   - Automatically adds relevant memories as system messages
   - Provides conversation continuity
   - Enables personalized responses

## API Reference

### ZepMemoryAgent

Main agent class that extends LiveKit's Agent with Zep memory capabilities.

```python
class ZepMemoryAgent(Agent):
    def __init__(
        self,
        zep_client: AsyncZep,
        user_id: str,
        thread_id: str,
        instructions: str = "You are a helpful assistant with memory."
    )
```

**Parameters:**
- `zep_client`: Initialized AsyncZep client instance
- `user_id`: User ID for memory isolation (required)
- `thread_id`: Thread ID for conversation continuity (required)
- `instructions`: System instructions for the agent

**Key Methods:**
- `clear_memory()`: Clear all memory for current thread
- `user_id`: Property to get current user ID
- `thread_id`: Property to get current thread ID

### ZepAgentSession

Session wrapper that automatically captures conversation events for Zep memory storage.

```python
class ZepAgentSession(AgentSession):
    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_id: str,
        thread_id: str,
        **kwargs
    )
```

**Parameters:**
- `zep_client`: Initialized AsyncZep client instance
- `user_id`: User ID for memory isolation (required)
- `thread_id`: Thread ID for conversation continuity (required)
- `**kwargs`: Standard AgentSession arguments (stt, llm, tts, vad, etc.)

**Features:**
- Automatically captures user and assistant messages through LiveKit's event system
- Stores conversations in Zep memory without manual intervention
- Maintains full compatibility with standard AgentSession API

**Note:** You must create the thread in Zep before instantiating the session.

### Memory Utilities

Additional utilities in `zep_livekit.memory`:

```python
from zep_livekit.memory import MemoryManager

# Advanced memory operations
memory_manager = MemoryManager(zep_client, user_id)
await memory_manager.search_relevant_memories("query", limit=5)
```

### Use Cases

**Perfect for:**
- 🎯 Voice assistants that remember user preferences
- 📞 Customer support with interaction history  
- 🎓 Educational agents tracking student progress
- 💼 Sales agents maintaining relationship context
- 🤖 Any conversational AI requiring memory continuity

## Development

### Setup Development Environment

```bash
git clone https://github.com/getzep/zep
cd zep/integrations/python/zep_livekit
make install
```

### Development Workflow

```bash
make format      # Format code
make lint        # Run linting
make type-check  # Run MyPy
make test        # Run tests
make pre-commit  # Full pre-commit checks
```

### Running Tests

```bash
# All tests
make test

# Skip integration tests (no API keys needed)
pytest -m "not integration"
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run `make pre-commit` to ensure code quality
5. Submit a pull request

## License

This project is licensed under the same terms as the main Zep project.

## Support

- 📖 [Documentation](https://help.getzep.com/integrations/livekit)
- 💬 [Discord Community](https://discord.gg/W8xaHrqWVc)
- 🐛 [Issue Tracker](https://github.com/getzep/zep/issues)
- 📧 [Email Support](mailto:support@getzep.com)

---

Built with ❤️ by the [Zep](https://www.getzep.com) team.