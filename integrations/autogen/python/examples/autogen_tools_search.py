import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import create_search_graph_tool


async def main() -> None:
    zep_client = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph = await zep_client.graph.create(name="Programming Knowledge")
    graph_uuid = str(graph.uuid_)

    for data in (
        "Python is useful for data science and AI.",
        "JavaScript is a language for web development.",
        "Rust provides memory safety without garbage collection.",
        "Go supports concurrent programming.",
    ):
        await zep_client.graph.episode.add(graph_uuid, type="text", data=data)

    search_tool = create_search_graph_tool(zep_client, graph_uuid=graph_uuid)
    agent = AssistantAgent(
        name="SearchAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        tools=[search_tool],
        system_message=(
            "Search the knowledge graph when the user asks for information. "
            "Summarize the search results."
        ),
        reflect_on_tool_use=True,
    )

    await Console(
        agent.run_stream(
            task="Search the knowledge base for information about Python and summarize it."
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
