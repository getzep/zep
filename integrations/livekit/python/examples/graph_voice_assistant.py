"""Run a LiveKit agent with Zep Context Graph memory.

Set ZEP_API_KEY, ZEP_GRAPH_UUID, OPENAI_API_KEY, and the LiveKit server
variables before you start the worker.
"""

import logging
import os

from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep

from zep_livekit import ZepGraphAgent, create_graph_search_tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def entrypoint(ctx: agents.JobContext) -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph_uuid = os.environ["ZEP_GRAPH_UUID"]
    await ctx.connect()

    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )
    search_tool = create_graph_search_tool(zep, graph_uuid=graph_uuid)
    agent = ZepGraphAgent(
        zep_client=zep,
        graph_uuid=graph_uuid,
        template_uuid=os.getenv("ZEP_TEMPLATE_UUID") or None,
        user_name="User",
        tools=[search_tool],
        instructions="Use the saved graph context to give helpful answers.",
    )
    logger.info("Starting the Zep graph-memory agent.")
    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Greet the user and ask what they would like to discuss.",
        allow_interruptions=True,
    )


if __name__ == "__main__":
    required = (
        "ZEP_API_KEY",
        "ZEP_GRAPH_UUID",
        "OPENAI_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Set the required environment variables: {', '.join(missing)}")
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
