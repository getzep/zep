"""Register an AG2 tool that searches a user's Zep graph."""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import create_search_memory_tool, create_user


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await create_user(zep, first_name="Alice", email="alice@example.com")
    graph_uuid = user.uuid_
    search = create_search_memory_tool(
        zep,
        graph_uuid,
        scope="auto",
    )
    assistant = AssistantAgent(
        name="researcher",
        llm_config=LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]}),
        system_message="Use the search_memory tool before answering.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )
    assistant.register_for_llm(description="Search memory")(search)
    user_proxy.register_for_execution()(search)
    try:
        user_proxy.initiate_chat(
            assistant,
            message="What do you know about Alice? Say TERMINATE when done.",
            max_turns=2,
        )
    finally:
        await zep.user.delete(graph_uuid)


if __name__ == "__main__":
    asyncio.run(main())
