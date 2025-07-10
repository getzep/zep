# Zep AutoGen Integration

This package provides integration between Zep and AutoGen frameworks, enabling persistent memory and context management for AI agents.

## Features

- **ZepMemory**: A memory implementation that integrates with Zep for persistent storage and retrieval of conversation context
- **Dual Storage Modes**:
  - **Message Storage**: When `user_id` is present in metadata, stores as messages in Zep sessions with `role_type="user"`
  - **Graph Storage**: When `user_id` is missing from metadata, stores as data in Zep's knowledge graph
- **AutoGen Compatibility**: Fully compatible with AutoGen's memory interface
- **Mime Type Validation**: Only accepts TEXT, MARKDOWN, and JSON content types
- **Persistent Storage**: Store and retrieve agent memories across sessions

## Installation

```bash
uv sync
```

## Usage

```python
import os
import uuid
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message
from zep_autogen import ZepMemory
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(
        api_key=os.environ.get('ZEP_API_KEY')
    )
    
    # Create user and session (one-time setup)
    user_id = "user123"
    session_id = uuid.uuid4().hex
    
    # Create user
    await zep_client.user.add(
        user_id=user_id,
        email="user@example.com",
        first_name="John",
        last_name="Doe"
    )
    
    # Create session
    await zep_client.memory.add_session(
        session_id=session_id,
        user_id=user_id
    )
    
    # Initialize Zep memory with the client
    memory = ZepMemory(
        client=zep_client,
        session_id=session_id,
        user_id=user_id  # Optional, enables graph search
    )
    
    # Create an agent with Zep memory
    agent = AssistantAgent(
        name="assistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4"),
        memory=[memory]
    )
    
    # Use the agent
    stream = agent.run_stream(task="What did we discuss about project planning?")
    
    # Don't forget to close the client when done
    await zep_client.close()

# Run the async function
import asyncio
asyncio.run(main())
```

## Development

Run tests:

```bash
pytest tests/
```

## License

This project follows the same license as the parent Zep project.