import asyncio
import os
import uuid

from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory, create_thread


async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    # Zep assigns the UUID of the user and of the thread. Keep these UUIDs in
    # your own database, and use them to address the resources later.
    # The user_id label is a temporary workaround for a production v4 defect:
    # a user created without a user_id gets 404 on thread operations. The label
    # is a name, not an address; the UUID still addresses the user.
    user = await zep_client.user.create(
        user_id=f"alice_{uuid.uuid4().hex[:8]}",
        email="alice@agents.local",
        first_name="Alice",
    )
    user_uuid = str(user.uuid_)
    print(f"Created user: {user_uuid}")

    thread = await create_thread(zep_client, user_uuid=user_uuid)
    thread_uuid = str(thread.uuid_)
    print(f"Created thread: {thread_uuid}")

    # Initialize Zep memory bound to the assistant
    memory = ZepUserMemory(client=zep_client, user_uuid=user_uuid, thread_uuid=thread_uuid)

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
        await add_message(user_msg1, "user", "Alice")
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
