"""
Voice Assistant Example with Zep Memory

This example demonstrates how to create a memory-enabled voice assistant using
Zep and LiveKit with OpenAI providers.

Before running:
1. Install dependencies: pip install zep-livekit
2. Set environment variables:
   - OPENAI_API_KEY: Your OpenAI API key
   - ZEP_API_KEY: Your Zep Cloud API key

Usage:
    python voice_assistant.py
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

from livekit import agents
from zep_cloud.client import AsyncZep
from livekit.plugins import openai, silero

from zep_livekit import ZepMemoryAgent, ZepAgentSession


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""
    
    # Use hardcoded user and thread with existing memories
    user_id = "paul_traveler1"
    thread_id = f"travel_chat_{uuid.uuid4().hex[:8]}"

    logger.info(f"Using existing user: {user_id}")
    logger.info(f"Using existing thread: {thread_id}")
    
    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # await zep_client.user.add(
    #     user_id=user_id,
    #     first_name="Paul",
    # )

    await zep_client.thread.create(
        thread_id=thread_id,
        user_id=user_id,
    )
    
    # Verify user exists in Zep (don't create, just verify)
    try:
        await zep_client.user.get(user_id)
        logger.info(f"✅ Confirmed user exists: {user_id}")
    except Exception as e:
        logger.error(f"❌ User {user_id} not found in Zep: {e}")
        logger.error("Please ensure the user exists before running")
        return
    
    # Verify thread exists in Zep (don't create, just verify)
    try:
        await zep_client.thread.get(thread_id)
        logger.info(f"✅ Confirmed thread exists: {thread_id}")
    except Exception as e:
        logger.error(f"❌ Thread {thread_id} not found in Zep: {e}")
        logger.error("Please ensure the thread exists before running")
        return
    
    # Connect to the room
    await ctx.connect()
    
    # Create memory-enabled agent (no providers, just instructions)
    agent = ZepMemoryAgent(
        zep_client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
        instructions="You are a helpful voice assistant with memory (provided as a system message on each chat turn). Always respond to user messages. Keep responses brief and conversational.",
    )
    
    logger.info(f"Created agent for user {user_id}, thread {thread_id}")
    
    # Create Zep-enabled session that captures conversation events
    session = ZepAgentSession(
        zep_client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
        # Standard LiveKit providers
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),  # Add VAD for streaming support
    )
    
    # Start the session with our memory-enabled agent using keyword argument
    await session.start(agent=agent, room=ctx.room)


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