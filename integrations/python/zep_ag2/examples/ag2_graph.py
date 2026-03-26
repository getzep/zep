"""
AG2 + Zep Knowledge Graph Example.

Demonstrates how to use ZepGraphMemoryManager to enrich an AG2 agent
with knowledge graph context and manage structured knowledge.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepGraphMemoryManager, create_add_graph_data_tool, create_search_graph_tool


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph_id = "company_knowledge_base"

    # Configure AG2 agents
    llm_config = LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]})

    assistant = AssistantAgent(
        name="knowledge_assistant",
        llm_config=llm_config,
        system_message="You are a knowledge management assistant. You can search and add information to a shared knowledge graph.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Create and register graph tools
    search_tool = create_search_graph_tool(zep, graph_id=graph_id)
    add_tool = create_add_graph_data_tool(zep, graph_id=graph_id)

    assistant.register_for_llm(description="Search the knowledge graph")(search_tool)
    user_proxy.register_for_execution()(search_tool)

    assistant.register_for_llm(description="Add data to the knowledge graph")(add_tool)
    user_proxy.register_for_execution()(add_tool)

    # Optionally enrich system message with existing knowledge
    graph_mgr = ZepGraphMemoryManager(zep, graph_id=graph_id)
    await graph_mgr.enrich_system_message(assistant, query="company policies")

    # Run conversation
    try:
        result = user_proxy.initiate_chat(
            assistant,
            message="Add this to our knowledge base: Our company uses Python and TypeScript as primary languages. Say TERMINATE when done.",
            max_turns=4,
        )
        print(result)
    except Exception as e:
        print(f"Conversation error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
