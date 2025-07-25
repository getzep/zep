import asyncio
import os
import uuid

from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import ZepMemory


async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    user_id = f"user_{uuid.uuid4().hex[:16]}"
    thread_id = f"thread_{uuid.uuid4().hex[:16]}"

    try:
        # Create user for the user (upfront initialization)
        await zep_client.user.add(
            user_id=user_id,
            email="alice@agents.local",
            first_name="Alice",
        )
        print(f"Created user: {user_id}")
    except Exception as e:
        print(f"User might already exist: {e}")

    try:
        # Create thread for this conversation
        await zep_client.thread.create(thread_id=thread_id, user_id=user_id)
        print(f"Created thread: {thread_id}")
    except Exception as e:
        print(f"Thread creation failed: {e}")

    # Initialize Zep memory bound to the assistant
    memory = ZepMemory(client=zep_client, thread_id=thread_id, user_id=user_id)

    # Create assistant agent with Zep memory
    agent = AssistantAgent(
        name="MemoryAwareAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        memory=[memory],
    )

    print("Assistant ready with Zep memory!")

    # Helper function to store individual messages in memory (AutoGen best practice)
    async def add_message(message: str, role: str, name: str | None = None):
        """Store a single message in memory following AutoGen standards"""
        metadata = {"type": "message", "role": role, "name": name}

        await memory.add(
            MemoryContent(content=message, mime_type=MemoryMimeType.TEXT, metadata=metadata)
        )

    # Example conversation with proper memory management
    try:
        print("\n=== Starting conversation with memory persistence ===")

        # First interaction
        user_msg1 = "My name is Alice and I love hiking in the mountains."
        print(f"User: {user_msg1}")
        await add_message(user_msg1, "user")
        response1 = await agent.run(task=user_msg1)
        agent_msg1 = response1.messages[-1].content
        print(f"Agent: {agent_msg1}")

        await add_message(agent_msg1, "assistant")

        # Second interaction - agent should remember Alice and her interests
        user_msg2 = "What outdoor activities do you think I'd enjoy?"
        print(f"\nUser: {user_msg2}")
        response2 = await agent.run(task=user_msg2)
        await add_message(user_msg2, "user")
        agent_msg2 = response2.messages[-1].content
        print(f"Agent: {agent_msg2}")
        await add_message(agent_msg2, "assistant")

        user_msg3 = "What's my name again?"
        print(f"\nUser: {user_msg3}")
        await add_message(user_msg3, "user")
        response3 = await agent.run(task=user_msg3)
        agent_msg3 = response3.messages[-1].content
        print(f"Agent: {agent_msg3}")

        await add_message(agent_msg3, "assistant")

        print("\n=== Memory persistence test complete ===")

    except Exception as e:
        print(f"Error during conversation: {e}")


if __name__ == "__main__":
    asyncio.run(main())
