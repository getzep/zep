"""
Basic AG2 + Zep Memory Example.

Demonstrates how to use ZepMemoryManager to enrich an AG2 agent's system
message with conversation memory and register memory tools for search/add.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os
import uuid

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, register_all_tools


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    user_id = f"user_{uuid.uuid4().hex[:16]}"
    session_id = f"thread_{uuid.uuid4().hex[:16]}"

    # Create user and thread in Zep
    try:
        await zep.user.add(user_id=user_id, email="alice@example.com", first_name="Alice")
        await zep.thread.create(thread_id=session_id, user_id=user_id)
        print(f"Created user {user_id} and thread {session_id}")
    except Exception as e:
        print(f"Setup: {e}")

    # Configure AG2 agents
    llm_config = LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]})

    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message="You are a helpful assistant with long-term memory.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Enrich agent with memory context
    memory_mgr = ZepMemoryManager(zep, user_id=user_id, session_id=session_id)
    await memory_mgr.enrich_system_message(assistant, query="conversation topic")

    # Register Zep tools so the agent can search and store memories
    register_all_tools(assistant, user_proxy, zep, user_id=user_id, session_id=session_id)

    # Run a conversation
    try:
        result = user_proxy.initiate_chat(
            assistant,
            message="My name is Alice and I love hiking in the mountains. Remember that! Say TERMINATE when done.",
            max_turns=4,
        )
        print(result)
    except Exception as e:
        print(f"Conversation error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
