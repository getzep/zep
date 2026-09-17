"""
AG2 + Zep Search Tool Example.

Demonstrates registering only the search tool on an AG2 agent,
useful for read-only knowledge retrieval scenarios.

The tool searches the graph of one Zep user. Zep v4 addresses that graph
by a server-generated UUID, which the application stores when it creates
the user. Set ZEP_USER_UUID to the UUID of an existing user.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import create_search_memory_tool


async def get_graph_uuid() -> str:
    """Read the graph UUID of the user.

    This function uses its own client, because an AsyncZep client binds to
    the event loop that first drives a request. The synchronous AG2 tools
    use a background loop, so the chat phase makes a second client.
    """
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await zep.user.get(os.environ["ZEP_USER_UUID"])
    return user.graph_uuid or ""


def main() -> None:
    graph_uuid = asyncio.run(get_graph_uuid())

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    llm_config = LLMConfig({"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]})

    assistant = AssistantAgent(
        name="researcher",
        llm_config=llm_config,
        system_message="You are a research assistant. Use the search_memory tool to find relevant information before answering questions.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Register only the search tool
    search_fn = create_search_memory_tool(zep, graph_uuid)
    assistant.register_for_llm(description="Search memory for relevant information")(search_fn)
    user_proxy.register_for_execution()(search_fn)

    try:
        result = user_proxy.initiate_chat(
            assistant,
            message="What do you know about my professional background? Say TERMINATE when done.",
            max_turns=4,
        )
        print(result)
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
