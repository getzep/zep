import os

from dotenv import load_dotenv
from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep

from zep_livekit import ZepUserAgent

load_dotenv()


async def entrypoint(ctx: agents.JobContext) -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_uuid = os.environ["ZEP_USER_UUID"]
    thread_uuid = os.environ["ZEP_THREAD_UUID"]
    await ctx.connect()

    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )
    agent = ZepUserAgent(
        zep_client=zep,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        user_message_name="User",
        assistant_message_name="Assistant",
        instructions="Give concise answers and use the saved user context.",
    )
    await session.start(agent=agent, room=ctx.room)


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
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit(f"Set the required environment variables: {', '.join(missing)}")
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
