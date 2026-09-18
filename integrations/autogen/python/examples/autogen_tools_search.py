import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import create_search_graph_tool


async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    # Zep assigns the UUID of the graph. Keep this UUID in your own database.
    graph = await zep_client.graph.create(name="Programming Knowledge")
    graph_uuid = str(graph.uuid_)
    print(f"Created graph: {graph_uuid}")

    # Pre-populate with some data for the search example
    for fact in (
        "Python is excellent for data science and AI",
        "JavaScript is the language of the web",
        "Rust provides memory safety without garbage collection",
        "Go is designed for concurrent programming",
    ):
        await zep_client.graph.episode.add(graph_uuid, type="text", data=fact)
    print("Pre-populated graph with programming knowledge")

    # Ingestion is asynchronous, so the example waits for the data to process.
    await asyncio.sleep(30)

    # Create search tool bound to the graph
    search_tool = create_search_graph_tool(zep_client, graph_uuid=graph_uuid)

    # Create assistant agent with search tool and reflection
    model_client = OpenAIChatCompletionClient(model="gpt-4.1-mini")
    agent = AssistantAgent(
        name="SearchAssistant",
        model_client=model_client,
        tools=[search_tool],
        system_message="You are a helpful assistant that can search knowledge bases. When you find information, summarize it clearly.",
        reflect_on_tool_use=True,
    )

    print("Assistant ready with Zep search tool!")

    try:
        print("\n=== Search Tool Demonstration ===")

        # First interaction
        user_msg1 = (
            "Search the knowledge base for information about Python and tell me what you find."
        )
        print(f"\nUser: {user_msg1}")

        # Use Console to get proper streaming output with tool reflection
        await Console(agent.run_stream(task=user_msg1))

        # Second interaction
        user_msg2 = "Now search for information about web development languages."
        print(f"\nUser: {user_msg2}")
        await Console(agent.run_stream(task=user_msg2))

        # Third interaction
        user_msg3 = "What can you find about systems programming languages?"
        print(f"\nUser: {user_msg3}")
        await Console(agent.run_stream(task=user_msg3))

        print("\n=== Search tool demonstration complete ===")

    except Exception as e:
        print(f"Error during conversation: {e}")


if __name__ == "__main__":
    asyncio.run(main())
