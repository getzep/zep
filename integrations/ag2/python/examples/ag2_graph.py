"""Create a shared Context Graph and expose search and add tools."""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import (
    ZepGraphMemoryManager,
    create_add_graph_data_tool,
    create_search_graph_tool,
)


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph = await zep.graph.create(name=f"ag2-example-{os.urandom(4).hex()}")
    graph_uuid = graph.uuid_

    assistant = AssistantAgent(
        name="knowledge_assistant",
        llm_config=LLMConfig({"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]}),
        system_message="Search the graph before you answer. Use the add tool to store facts.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )
    search = create_search_graph_tool(zep, graph_uuid)
    add = create_add_graph_data_tool(zep, graph_uuid)
    assistant.register_for_llm(description="Search the knowledge graph")(search)
    user_proxy.register_for_execution()(search)
    assistant.register_for_llm(description="Add data to the knowledge graph")(add)
    user_proxy.register_for_execution()(add)

    manager = ZepGraphMemoryManager(zep, graph_uuid)
    await manager.add_data("The example project uses Python.")
    await manager.enrich_system_message(assistant, query="What language does the project use?")
    user_proxy.initiate_chat(
        assistant,
        message="What language does the project use? Say TERMINATE when done.",
        max_turns=2,
    )


if __name__ == "__main__":
    asyncio.run(main())
