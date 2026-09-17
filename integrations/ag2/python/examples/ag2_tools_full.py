"""
AG2 + Zep Full Tool Suite Example.

Demonstrates registering all Zep tools (search memory, add memory,
search graph, add graph data) and using them in a GroupChat with
multiple agents.

Zep v4 addresses every user, thread, and graph by a server-generated UUID.
The example creates the user and the thread one time and keeps the UUIDs
from the responses.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os

from autogen import AssistantAgent, GroupChat, GroupChatManager, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import create_thread, create_user, register_all_tools


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    # Create the user and the thread one time, and keep their UUIDs.
    user = await create_user(zep, first_name="Bob", email="bob@example.com")
    thread = await create_thread(zep, user_uuid=user.uuid_ or "")

    graph_uuid = user.graph_uuid or ""
    thread_uuid = thread.uuid_ or ""

    llm_config = LLMConfig({"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]})

    researcher = AssistantAgent(
        name="researcher",
        llm_config=llm_config,
        system_message="You are a researcher. Search memory and knowledge graphs to find information.",
    )
    writer = AssistantAgent(
        name="writer",
        llm_config=llm_config,
        system_message="You are a writer. Use the add tools to store important information.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Register all tools on both agents, with user_proxy as executor
    register_all_tools(researcher, user_proxy, zep, graph_uuid, thread_uuid)
    register_all_tools(writer, user_proxy, zep, graph_uuid, thread_uuid)

    # Create a group chat
    group_chat = GroupChat(
        agents=[user_proxy, researcher, writer],
        messages=[],
        max_round=6,
    )
    manager = GroupChatManager(groupchat=group_chat, llm_config=llm_config)

    try:
        user_proxy.initiate_chat(
            manager,
            message="Add this fact: Bob is a data scientist who specializes in NLP. Then search for what you know about Bob.",
        )
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
