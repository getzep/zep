import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import create_add_graph_data_tool, create_search_graph_tool, create_user


async def main() -> None:
    zep_client = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await create_user(
        zep_client,
        first_name="Alice",
        email="alice@example.com",
    )
    user_uuid = str(user.uuid_)

    search_tool = create_search_graph_tool(zep_client, user_uuid=user_uuid)
    add_tool = create_add_graph_data_tool(zep_client, user_uuid=user_uuid)
    agent = AssistantAgent(
        name="KnowledgeAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        tools=[search_tool, add_tool],
        system_message=(
            "You can add information to and search the user's knowledge graph. "
            "Use the tools when needed, then summarize the result."
        ),
        reflect_on_tool_use=True,
    )

    await Console(
        agent.run_stream(
            task=(
                "Add this fact: Alice is a senior frontend developer who uses React. "
                "Then search for Alice's role."
            )
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
