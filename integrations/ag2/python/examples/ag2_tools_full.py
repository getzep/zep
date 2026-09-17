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
import uuid

from autogen import AssistantAgent, GroupChat, GroupChatManager, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import create_thread, create_user, register_all_tools


async def provision() -> tuple[str, str]:
    """Create the user and the thread, and return the graph and thread UUIDs.

    This function uses its own client, because an AsyncZep client binds to
    the event loop that first drives a request. The synchronous AG2 tools
    use a background loop, so the chat phase makes a second client.
    """
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    # Create the user and the thread one time, and keep their UUIDs.
    #
    # The user_id label is a temporary workaround for a defect in the
    # production v4 API, which rejects thread.add_messages and
    # thread.get_context for a user that has no label. The label is not an
    # address: the example uses user.uuid_ for every later call.
    user = await create_user(
        zep,
        user_id=f"ag2-tools-full-{uuid.uuid4().hex[:8]}",
        first_name="Bob",
        email="bob@example.com",
    )
    thread = await create_thread(zep, user_uuid=user.uuid_ or "")
    return user.graph_uuid or "", thread.uuid_ or ""


def main() -> None:
    graph_uuid, thread_uuid = asyncio.run(provision())

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

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
    main()
