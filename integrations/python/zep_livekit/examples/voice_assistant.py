"""
Voice Assistant Example with Zep Memory

This example demonstrates how to create a memory-enabled voice assistant using
Zep and LiveKit.

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

from zep_livekit import ZepUserAgent

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
ZEP_USER_ID = "mark_traveler"
ZEP_THREAD_ID = f"travel_chat_session_{uuid.uuid4().hex[:8]}"


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""

    logger.info(f"Starting Zep memory-enabled agent for user: {ZEP_USER_ID}")

    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Ensure user and thread exist
    try:
        await zep_client.user.get(user_id=ZEP_USER_ID)
        logger.info(f"✅ User {ZEP_USER_ID} exists")
    except Exception:
        # Create user if doesn't exist
        await zep_client.user.add(
            user_id=ZEP_USER_ID,
            first_name="Mark",
        )
        logger.info(f"✅ Created user {ZEP_USER_ID}")

    await zep_client.thread.create(
        thread_id=ZEP_THREAD_ID,
        user_id=ZEP_USER_ID,
    )

    # Connect to room
    await ctx.connect()

    # Create AgentSession with providers
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )

    # Create the memory-enabled agent
    agent = ZepUserAgent(
        zep_client=zep_client,
        user_id=ZEP_USER_ID,
        thread_id=ZEP_THREAD_ID,
        user_message_name="Mark the traveler",
        assistant_message_name="TravelBot",
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
