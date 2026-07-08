"""
Voice Assistant Example with Zep Memory

This example demonstrates how to create a memory-enabled voice assistant using
Zep and LiveKit, including:

- Out-of-band provisioning with `ensure_user`/`ensure_thread` and an
  `on_created` hook that seeds per-user setup exactly once.
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
import uuid

from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep

from zep_livekit import ZepUserAgent, create_graph_search_tool, ensure_thread, ensure_user

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
ZEP_USER_ID = "mark_traveler"
ZEP_THREAD_ID = f"travel_chat_session_{uuid.uuid4().hex[:8]}"


async def seed_user_preferences(zep_client: AsyncZep, user_id: str) -> None:
    """`on_created` hook: runs exactly once, right after the user is first created."""
    logger.info(f"Running first-time setup for new user: {user_id}")
    # e.g. seed initial facts, set custom instructions, configure ontology, etc.


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""

    logger.info(f"Starting Zep memory-enabled agent for user: {ZEP_USER_ID}")

    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Provision the Zep user and thread out-of-band, before the first turn.
    # `on_created` fires only the first time this user is created.
    await ensure_user(
        zep_client,
        user_id=ZEP_USER_ID,
        first_name="Mark",
        on_created=seed_user_preferences,
    )
    await ensure_thread(zep_client, thread_id=ZEP_THREAD_ID, user_id=ZEP_USER_ID)

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
    search_tool = create_graph_search_tool(zep_client, user_id=ZEP_USER_ID)

    # Create the memory-enabled agent
    agent = ZepUserAgent(
        zep_client=zep_client,
        user_id=ZEP_USER_ID,
        thread_id=ZEP_THREAD_ID,
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
