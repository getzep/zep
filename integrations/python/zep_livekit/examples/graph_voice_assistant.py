"""
Graph Voice Assistant Example with Zep Memory

This example demonstrates how to create a knowledge-based voice assistant using
Zep's graph memory and LiveKit.

The agent stores conversations in a shared Zep knowledge graph for persistent
memory across sessions. Perfect for building AI assistants that can learn and
remember information from conversations.

Before running:
1. Install dependencies: pip install zep-livekit
2. Set environment variables:
   - OPENAI_API_KEY: Your OpenAI API key
   - ZEP_API_KEY: Your Zep Cloud API key

Usage:
    python graph_voice_assistant.py
"""

import logging
import os
import uuid

from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep

from zep_livekit import ZepGraphAgent

# Setup logging
logging.basicConfig(level=logging.DEBUG)  # Changed to DEBUG to see participant detection
logger = logging.getLogger(__name__)

# Configuration
ZEP_GRAPH_ID = f"knowledge_graph_{uuid.uuid4().hex[:8]}"


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""

    logger.info(f"Starting Zep graph-enabled agent for graph: {ZEP_GRAPH_ID}")

    # Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Create graph if it doesn't exist
    try:
        await zep_client.graph.get(ZEP_GRAPH_ID)
        logger.info(f"✅ Graph {ZEP_GRAPH_ID} exists")
    except Exception:
        # Create graph if doesn't exist
        await zep_client.graph.create(
            graph_id=ZEP_GRAPH_ID,
            name="Knowledge Graph for Voice Assistant",
            description="Graph-based knowledge storage for conversational AI",
        )
        logger.info(f"✅ Created graph {ZEP_GRAPH_ID}")

    # Connect to room
    await ctx.connect()

    # Create AgentSession with providers
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )

    # Create the graph-based memory agent
    agent = ZepGraphAgent(
        zep_client=zep_client,
        graph_id=ZEP_GRAPH_ID,
        user_name="Mark",
        facts_limit=15,  # Number of facts to retrieve
        entity_limit=5,  # Number of entities to retrieve
        episode_limit=2, # Number of episodes to retrieve
        instructions="""
            You are a knowledgeable assistant with access to a persistent knowledge graph.
            You can learn and remember facts, relationships, and concepts from our conversations.
            When users share information, you'll store it in your knowledge graph.
            When they ask questions, you'll search your knowledge to provide informed answers.

            Your knowledge grows with each conversation, making you more helpful over time.
            Use the context provided in system messages to give accurate, informed responses.
        """,
    )

    logger.info("Starting session with graph-enabled agent...")

    # Start the session with the agent
    await session.start(agent=agent, room=ctx.room)

    # Initial greeting for knowledge-based assistant
    await session.generate_reply(
        instructions="Greet the user as a knowledgeable assistant. Explain that you have access to a persistent knowledge graph where you can learn and remember information from conversations. Ask them what they'd like to discuss or learn about.",
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
