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
import asyncio
from zep_cloud.client import AsyncZep
from zep_autogen import ZepMemory
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(api_key=os.environ.get('ZEP_API_KEY'))
    
    # Setup Zep infrastructure upfront
    assistant_id = "assistant_001"
    user_id = f"user_{assistant_id}"
    session_id = f"session_{assistant_id}_{uuid.uuid4().hex[:8]}"
    
    try:
        # Create user for the assistant (upfront initialization)
        await zep_client.user.add(
            user_id=user_id,
            email=f"{assistant_id}@agents.local",
            first_name="Assistant",
            last_name=assistant_id
        )
        print(f"Created user: {user_id}")
    except Exception as e:
        print(f"User might already exist: {e}")
    
    try:
        # Create session for this conversation
        await zep_client.memory.add_session(
            session_id=session_id,
            user_id=user_id
        )
        print(f"Created session: {session_id}")
    except Exception as e:
        print(f"Session creation failed: {e}")
    
    # Initialize Zep memory bound to the assistant
    memory = ZepMemory(
        client=zep_client,
        session_id=session_id,
        user_id=user_id
    )
    
    # Create assistant agent with Zep memory
    agent = AssistantAgent(
        name=assistant_id,
        model_client=OpenAIChatCompletionClient(model="gpt-4o-mini"),
        memory=[memory]
    )
    
    print(f"Assistant {assistant_id} ready with Zep memory!")
    
    # Example conversation
    try:
        # First interaction
        response1 = await agent.run("My name is Alice and I love hiking in the mountains.")
        print(f"Agent: {response1.messages[-1].content}")
        
        # Second interaction - agent should remember Alice and her interests
        response2 = await agent.run("What outdoor activities do you think I'd enjoy?")
        print(f"Agent: {response2.messages[-1].content}")
        
        # Third interaction - test memory persistence
        response3 = await agent.run("What's my name again?")
        print(f"Agent: {response3.messages[-1].content}")
        
    except Exception as e:
        print(f"Error during conversation: {e}")
    
    finally:
        # Clean up
        await zep_client.close()
        print("Zep client closed")

if __name__ == "__main__":
    asyncio.run(main())
```

### Key Features in This Example

- **Single Zep Client**: One `AsyncZep` client manages all operations
- **Assistant-Bound Memory**: Memory is tied to specific assistant ID
- **Upfront Initialization**: User and session creation handled before agent creation
- **Session Isolation**: Each conversation gets a unique session
- **Memory Persistence**: Agent remembers information across interactions
- **Proper Cleanup**: Client resources are properly closed

## Development

Run tests:

```bash
pytest tests/
```

## License

This project follows the same license as the parent Zep project.