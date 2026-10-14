"""Create a Zep user and thread, then attach memory to an AG2 agent."""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, create_thread, create_user, register_all_tools


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await create_user(zep, first_name="Alice", email="alice@example.com")
    thread = await create_thread(zep, user_uuid=user.uuid_)
    user_uuid = user.uuid_
    graph_uuid = user.uuid_
    thread_uuid = thread.uuid_

    llm_config = LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]})
    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message="Use the supplied Zep context to answer questions about Alice.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )
    memory = ZepMemoryManager(
        zep,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        graph_uuid=graph_uuid,
    )
    memory.attach_to_agent(assistant)
    register_all_tools(assistant, user_proxy, zep, graph_uuid, thread_uuid)

    try:
        user_proxy.initiate_chat(
            assistant,
            message="I love hiking. Remember that and say TERMINATE.",
            max_turns=3,
        )
    finally:
        await zep.thread.delete(thread_uuid)
        await zep.user.delete(user_uuid)


if __name__ == "__main__":
    asyncio.run(main())
