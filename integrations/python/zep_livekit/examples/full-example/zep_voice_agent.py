import os
import uuid
from dotenv import load_dotenv

from livekit import agents
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep
from zep_livekit import ZepUserAgent

# Load environment variables
load_dotenv()

# Constants
USER_ID = "John-1234"
THREAD_ID = f"conversation-{uuid.uuid4().hex[:8]}"
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"
USER_EMAIL = "john.doe@example.com"

async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""
    
    # Step 1: Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
    
    # Step 2: Create or get user
    try:
        await zep_client.user.get(user_id=USER_ID)
    except Exception:
        await zep_client.user.add(
            user_id=USER_ID,
            first_name=USER_FIRST_NAME,
            last_name=USER_LAST_NAME,
            email=USER_EMAIL,
        )

    # Step 3: Create new thread for this session
    await zep_client.thread.create(
        thread_id=THREAD_ID,
        user_id=USER_ID
    )

    # Step 4: Connect to LiveKit room
    await ctx.connect()
    
    # Step 5: Create agent session with OpenAI components
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )

    # Step 6: Create the memory-enabled agent
    agent = ZepUserAgent(
        zep_client=zep_client,
        user_id=USER_ID,
        thread_id=THREAD_ID,
        user_message_name=USER_FIRST_NAME,
        assistant_message_name="Assistant",
        instructions=f"""You are a helpful assistant who responds concisely in at most 1 sentence for each response. If the user asks you to complete a task of any kind, such as playing music or using any other kind of tool, pretend that you can in fact do that task for simulation purposes."""
    )

    # Step 7: Start the session
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    # Validate environment variables
    required_vars = ["OPENAI_API_KEY", "ZEP_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"❌ Missing required environment variables: {missing_vars}")
        exit(1)
    
    # Start the LiveKit agent
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )