"""
AG2 + Zep Knowledge Graph Example.

Demonstrates how to use ZepGraphMemoryManager to enrich an AG2 agent
with knowledge graph context and manage structured knowledge.

Zep v4 addresses every graph by a server-generated UUID. The example
creates the graph one time and keeps the UUID from the response. A
production application stores that UUID in its own database.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepGraphMemoryManager, create_add_graph_data_tool, create_search_graph_tool


async def provision() -> str:
    """Create the graph and return its UUID.

    This function uses its own client, because an AsyncZep client binds to
    the event loop that first drives a request. The synchronous AG2 tools
    use a background loop, so the chat phase makes a second client.
    """
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph = await zep.graph.create(name="company_knowledge_base")
    return graph.uuid_ or ""


def main() -> None:
    graph_uuid = asyncio.run(provision())

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    # Configure AG2 agents
    llm_config = LLMConfig({"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]})

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
    search_tool = create_search_graph_tool(zep, graph_uuid)
    add_tool = create_add_graph_data_tool(zep, graph_uuid)

    assistant.register_for_llm(description="Search the knowledge graph")(search_tool)
    user_proxy.register_for_execution()(search_tool)

    assistant.register_for_llm(description="Add data to the knowledge graph")(add_tool)
    user_proxy.register_for_execution()(add_tool)

    # Optionally enrich system message with existing knowledge
    # The chat runs in synchronous code, so the example uses the
    # synchronous search wrapper, which drives the background loop.
    graph_mgr = ZepGraphMemoryManager(zep, graph_uuid)
    results = graph_mgr.search_sync("company policies", limit=5)
    if results:
        facts = "\n".join(f"- {r['content']}" for r in results)
        assistant.update_system_message(
            f"{assistant.system_message}\n\n## Knowledge Graph Context\n{facts}"
        )

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
    main()
