# Zep AutoGen Integration

A dedicated integration package that enables [Zep](https://getzep.com) to work seamlessly with [Microsoft AutoGen](https://github.com/microsoft/autogen) agents, providing persistent conversation memory and intelligent context retrieval.

## Installation

```bash
pip install zep-autogen
```

## Quick Start

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_autogen import ZepMemory
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main():
    # Initialize Zep client
    zep_client = AsyncZep(api_key="your-zep-api-key")
    
    # Create Zep memory for your agent
    memory = ZepMemory(
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

## Features

- **Persistent Memory**: Conversations persist across agent sessions
- **Intelligent Retrieval**: Zep's memory automatically provides relevant context
- **AutoGen Compatible**: Seamlessly integrates with AutoGen's memory interface
- **Async Support**: Full async/await support for modern applications
- **Type Safety**: Fully typed with comprehensive type hints

## Configuration

### Environment Variables

```bash
# Required: Your Zep Cloud API key
export ZEP_API_KEY="your-zep-api-key"
```

### ZepMemory Parameters

- `client` (AsyncZep): Your Zep client instance
- `user_id` (str): Unique identifier for the user
- `thread_id` (str, optional): Thread/conversation identifier
- `memory_type` (str, optional): Type of memory to use (default: "perpetual")
- `max_tokens` (int, optional): Maximum tokens to retrieve (default: 4000)

## Examples

### Basic Usage

See [examples/autogen_basic.py](examples/autogen_basic.py) for a complete working example.

### Multi-Agent with Shared Memory

```python
# Multiple agents can share the same memory context
shared_memory = ZepMemory(
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

## Requirements

- Python 3.10+
- `zep-cloud>=2.15.0`
- `autogen-agentchat>=0.6.1`
- `autogen-core>=0.6.1`

## License

MIT License - see [LICENSE](LICENSE) for details.

## Support

- [Zep Documentation](https://help.getzep.com)
- [AutoGen Documentation](https://microsoft.github.io/autogen/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.