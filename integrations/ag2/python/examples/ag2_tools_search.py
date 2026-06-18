"""
AG2 + Zep Search Tool Example.

Demonstrates registering only the search tool on an AG2 agent,
useful for read-only knowledge retrieval scenarios.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import create_search_memory_tool


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_id = "user_alice"

    llm_config = LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]})

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
    search_fn = create_search_memory_tool(zep, user_id=user_id)
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
    asyncio.run(main())
