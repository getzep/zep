"""Run a LiveKit agent with Zep thread memory.

Set ZEP_API_KEY, ZEP_USER_UUID, ZEP_THREAD_UUID, OPENAI_API_KEY, and the
LiveKit server variables before you start the worker.
"""

import logging
import os

from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep

from zep_livekit import ZepUserAgent, create_graph_search_tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def entrypoint(ctx: agents.JobContext) -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_uuid = os.environ["ZEP_USER_UUID"]
    thread_uuid = os.environ["ZEP_THREAD_UUID"]
    await ctx.connect()

    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )
    search_tool = create_graph_search_tool(zep, graph_uuid=user_uuid)
    agent = ZepUserAgent(
        zep_client=zep,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        user_message_name="User",
        assistant_message_name="Assistant",
        tools=[search_tool],
        instructions="Use the saved user context to give helpful answers.",
    )
    logger.info("Starting the Zep thread-memory agent.")
    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Greet the user and ask how you can help.",
        allow_interruptions=True,
    )


if __name__ == "__main__":
    required = (
        "ZEP_API_KEY",
        "ZEP_USER_UUID",
        "ZEP_THREAD_UUID",
        "OPENAI_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Set the required environment variables: {', '.join(missing)}")
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
