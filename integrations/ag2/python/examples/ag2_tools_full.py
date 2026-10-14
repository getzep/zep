"""Register all Zep tools on a multi-agent AG2 conversation."""

import asyncio
import os

from autogen import AssistantAgent, GroupChat, GroupChatManager, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import create_thread, create_user, register_all_tools


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await create_user(
        zep,
        first_name="Bob",
        email=f"ag2-tools-{os.urandom(4).hex()}@example.com",
    )
    user_uuid = user.uuid_
    thread = await create_thread(zep, user_uuid=user_uuid)
    thread_uuid = thread.uuid_
    graph_uuid = user_uuid

    llm_config = LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]})
    researcher = AssistantAgent(
        name="researcher",
        llm_config=llm_config,
        system_message="Search memory and the graph for relevant facts.",
    )
    writer = AssistantAgent(
        name="writer",
        llm_config=llm_config,
        system_message="Use the add tools to store important facts.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )
    register_all_tools(researcher, user_proxy, zep, graph_uuid, thread_uuid)
    register_all_tools(writer, user_proxy, zep, graph_uuid, thread_uuid)

    group_chat = GroupChat(
        agents=[user_proxy, researcher, writer],
        messages=[],
        max_round=6,
    )
    manager = GroupChatManager(groupchat=group_chat, llm_config=llm_config)
    try:
        user_proxy.initiate_chat(
            manager,
            message="Store that Bob works in data science, then search for that fact.",
        )
    finally:
        await zep.thread.delete(thread_uuid)
        await zep.user.delete(user_uuid)


if __name__ == "__main__":
    asyncio.run(main())
