import asyncio
import os
import uuid

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud.client import AsyncZep

from zep_autogen import create_add_graph_data_tool, create_search_graph_tool


async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    user_id = f"user_{uuid.uuid4().hex[:16]}"

    try:
        # Create user for the example
        await zep_client.user.add(
            user_id=user_id,
            email="alice@example.com",
            first_name="Alice",
        )
        print(f"Created user: {user_id}")

    except Exception as e:
        print(f"User creation failed or user already exists: {e}")

    # Create both tools bound to the user
    search_tool = create_search_graph_tool(zep_client, user_id=user_id)
    add_tool = create_add_graph_data_tool(zep_client, user_id=user_id)

    # Create assistant agent with both tools and reflection
    model_client = OpenAIChatCompletionClient(model="gpt-4.1-mini")
    agent = AssistantAgent(
        name="KnowledgeAssistant",
        model_client=model_client,
        tools=[search_tool, add_tool],
        system_message="You are a helpful assistant that can add data to and search user knowledge graphs. When you find information, summarize it clearly.",
        reflect_on_tool_use=True,
    )

    print("Assistant ready with Zep search and add tools!")

    try:
        print("\n=== Full Tools Demonstration ===")

        # Add personal information about the user
        user_msg1 = "Please add this information about me: 'Alice works as a Senior Frontend Developer at TechCorp and has 5 years of experience with React'"
        print(f"\nUser: {user_msg1}")
        await Console(agent.run_stream(task=user_msg1))

        # Add work preferences
        user_msg2 = "Also add: 'Alice prefers TypeScript over JavaScript for large projects because it helps catch errors early'"
        print(f"\nUser: {user_msg2}")
        await Console(agent.run_stream(task=user_msg2))

        # Add learning goals
        user_msg3 = "And add: 'Alice is currently learning Vue.js and wants to transition to full-stack development using Node.js'"
        print(f"\nUser: {user_msg3}")
        await Console(agent.run_stream(task=user_msg3))

        # Add hobby/side project info
        user_msg4 = "Also add: 'Alice is building a personal portfolio website and contributing to open source projects on weekends'"
        print(f"\nUser: {user_msg4}")
        await Console(agent.run_stream(task=user_msg4))

        # Wait for indexing
        print("\nWaiting for data to be indexed...")
        await asyncio.sleep(10)

        # Now search for professional information
        user_msg5 = "What do you know about my professional background and current skills?"
        print(f"\nUser: {user_msg5}")
        await Console(agent.run_stream(task=user_msg5))

        # Search for learning goals
        user_msg6 = "What are my current learning goals and career interests?"
        print(f"\nUser: {user_msg6}")
        await Console(agent.run_stream(task=user_msg6))

        # Add and search in one request
        user_msg7 = "Add this new information: 'Alice just completed a Node.js certification course last month', then tell me about my overall learning progress"
        print(f"\nUser: {user_msg7}")
        await Console(agent.run_stream(task=user_msg7))

        print("\n=== Full tools demonstration complete ===")

    except Exception as e:
        print(f"Error during conversation: {e}")


if __name__ == "__main__":
    asyncio.run(main())
