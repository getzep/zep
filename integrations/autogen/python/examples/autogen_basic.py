import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory, create_thread, create_user


async def main() -> None:
    zep_client = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await create_user(
        zep_client,
        first_name="Alice",
        email="alice@agents.local",
    )
    user_uuid = str(user.uuid_)
    thread = await create_thread(zep_client, user_uuid=user_uuid)
    memory = ZepUserMemory(
        client=zep_client,
        user_uuid=user_uuid,
        thread_uuid=str(thread.uuid_),
    )
    agent = AssistantAgent(
        name="MemoryAwareAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        memory=[memory],
    )

    async def add_message(message: str, role: str, name: str | None = None) -> None:
        await memory.add(
            MemoryContent(
                content=message,
                mime_type=MemoryMimeType.TEXT,
                metadata={"type": "message", "role": role, "name": name},
            )
        )

    for user_message in (
        "My name is Alice and I love hiking in the mountains.",
        "What outdoor activities do you think I would enjoy?",
        "What is my name?",
    ):
        await add_message(user_message, "user", "Alice")
        response = await agent.run(task=user_message)
        assistant_message = str(response.messages[-1].content)
        print(f"Assistant: {assistant_message}")
        await add_message(assistant_message, "assistant")


if __name__ == "__main__":
    asyncio.run(main())
