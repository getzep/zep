"""
Voice Assistant Example with Zep Memory

This example demonstrates how to create a memory-enabled voice assistant using
Zep and LiveKit, including:

- Out-of-band provisioning with `create_user`/`create_thread` and an
  `on_created` hook that seeds per-user setup one time. Zep v4 addresses
  every resource by a server-generated UUID, so the application creates the
  resources one time, stores the UUIDs, and gives the UUIDs to the agent.
  A production application reads the stored UUIDs from its own database
  instead of creating a new user on each run.
- A model-callable graph-search tool (`create_graph_search_tool`) the agent
  can call on demand, alongside the automatic per-turn context injection.

Before running:
1. Install dependencies: pip install zep-livekit
2. Set environment variables:
   - OPENAI_API_KEY: Your OpenAI API key
   - ZEP_API_KEY: Your Zep Cloud API key

Usage:
    python voice_assistant.py
"""

import logging
import os

from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep

from zep_livekit import ZepUserAgent, create_graph_search_tool, create_thread, create_user

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration. Zep v4 addresses a user by a server-generated UUID, so there
# is no user name constant here. The example creates the user and prints the
# UUID.
USER_FIRST_NAME = "Mark"


async def seed_user_preferences(zep_client: AsyncZep, user_uuid: str) -> None:
    """`on_created` hook: runs one time, directly after the user is created."""
    logger.info(f"Running first-time setup for new user: {user_uuid}")
    # e.g. seed initial facts, set custom instructions, configure ontology, etc.


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""

    logger.info("Starting Zep memory-enabled agent")

    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Provision the Zep user and thread out-of-band, before the first turn, and
    # read the UUID of each resource from the response. A production
    # application does this one time and stores the UUIDs in its own database.
    user = await create_user(
        zep_client,
        first_name=USER_FIRST_NAME,
        on_created=seed_user_preferences,
    )
    user_uuid = user.uuid_ or ""
    graph_uuid = user.graph_uuid or ""
    thread = await create_thread(zep_client, user_uuid=user_uuid)
    thread_uuid = thread.uuid_ or ""
    logger.info(f"Created Zep user {user_uuid} and thread {thread_uuid}")

    # Connect to room
    await ctx.connect()

    # Create AgentSession with providers
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )

    # A model-callable tool letting the agent search the user's graph on
    # demand, in addition to the context injected automatically every turn.
    search_tool = create_graph_search_tool(zep_client, graph_uuid=graph_uuid)

    # Create the memory-enabled agent
    agent = ZepUserAgent(
        zep_client=zep_client,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        user_message_name="Mark the traveler",
        assistant_message_name="TravelBot",
        tools=[search_tool],
        instructions="You are a helpful travel assistant with persistent memory. Rely user context to provide personalized travel recommendations and planning advice.",
    )

    logger.info("Starting session with memory-enabled agent...")

    # Start the session with the agent
    await session.start(agent=agent, room=ctx.room)

    # Initial greeting
    await session.generate_reply(
        instructions="Greet the user warmly as a travel assistant and ask how you can help them plan their next trip.",
        allow_interruptions=True,
    )


if __name__ == "__main__":
    # Validate required environment variables
    required_env_vars = ["OPENAI_API_KEY", "ZEP_API_KEY"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        exit(1)

    # Start the LiveKit agent
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
