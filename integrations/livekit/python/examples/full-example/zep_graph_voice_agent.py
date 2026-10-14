import os

from dotenv import load_dotenv
from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep

from zep_livekit import ZepGraphAgent

load_dotenv()


async def entrypoint(ctx: agents.JobContext) -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph_uuid = os.environ["ZEP_GRAPH_UUID"]
    await ctx.connect()

    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )
    agent = ZepGraphAgent(
        zep_client=zep,
        graph_uuid=graph_uuid,
        template_uuid=os.getenv("ZEP_TEMPLATE_UUID") or None,
        user_name="User",
        search_filters=SearchFilters(node_labels=["Restaurant"]),
        max_characters=6000,
        instructions="Give concise answers and use the saved graph context.",
    )
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    required = (
        "ZEP_API_KEY",
        "ZEP_GRAPH_UUID",
        "OPENAI_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit(f"Set the required environment variables: {', '.join(missing)}")
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
