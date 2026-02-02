"""
Set up a test user in Zep with some facts/memories.

Run this first to populate Zep with data that the proxy can retrieve.
"""

import asyncio
import os
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

load_dotenv()


async def setup_test_user():
    """Create a test user and thread with some memories in Zep."""

    zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    user_id = "test-user-123"
    thread_id = "test-thread-123"

    print(f"Setting up test user: {user_id}")
    print("=" * 50)

    # 1. Create or get the user
    try:
        user = await zep.user.get(user_id)
        print(f"User already exists: {user.user_id}")
    except Exception:
        print("Creating new user...")
        user = await zep.user.add(
            user_id=user_id,
            first_name="Randy",
            last_name="Adams",
            email="randy@talk2me.example.com",
            metadata={
                "company": "Talk2Me",
                "role": "Founder",
                "interests": ["AI", "voice technology", "celebrities"]
            }
        )
        print(f"Created user: {user.user_id}")

    # 2. Create a thread for this user
    try:
        thread = await zep.thread.get(thread_id)
        print(f"Thread already exists: {thread_id}")
    except Exception:
        print("Creating new thread...")
        thread = await zep.thread.create(
            thread_id=thread_id,
            user_id=user_id
        )
        print(f"Created thread: {thread_id}")

    # 3. Add some conversation history that will generate facts
    print("\nAdding conversation history...")

    messages = [
        Message(
            role="user",
            role_type="user",
            content="Hi, I'm Randy. I run a company called Talk2Me where we build voice AI agents for celebrities."
        ),
        Message(
            role="assistant",
            role_type="assistant",
            content="Nice to meet you, Randy! Talk2Me sounds fascinating. Building voice AI for celebrities must involve some interesting challenges with authenticity and personality matching."
        ),
        Message(
            role="user",
            role_type="user",
            content="Yes, we work with Tim Draper, Kelsey Plum, and several others. We're launching four new celebrity voices in February."
        ),
        Message(
            role="assistant",
            role_type="assistant",
            content="That's impressive! Tim Draper and Kelsey Plum are quite different personalities. The February launch sounds exciting - four new voices is ambitious but shows great momentum."
        ),
        Message(
            role="user",
            role_type="user",
            content="I used to work at NeXT with Steve Jobs. Back when there were only 11 people at the company."
        ),
        Message(
            role="assistant",
            role_type="assistant",
            content="Wow, that's incredible history! Working alongside Steve Jobs in the early NeXT days must have been an extraordinary experience. That perspective probably gives you unique insights into building innovative technology products."
        ),
        Message(
            role="user",
            role_type="user",
            content="We're concerned about latency in our voice agents. Two seconds is too long for a response."
        ),
        Message(
            role="assistant",
            role_type="assistant",
            content="Latency is critical for voice - two seconds definitely breaks the natural flow of conversation. Most successful voice AI aims for under 500ms response time. Are you seeing the delay in the LLM, TTS, or somewhere else in the pipeline?"
        ),
    ]

    try:
        await zep.thread.add_messages(thread_id=thread_id, messages=messages)
        print(f"Added {len(messages)} messages to thread")
    except Exception as e:
        print(f"Note: Messages may already exist or error occurred: {e}")

    # 4. Wait a moment for Zep to process and extract facts
    print("\nWaiting for Zep to process and extract facts...")
    await asyncio.sleep(5)

    # 5. Retrieve and display what we stored
    print("\n" + "=" * 50)
    print("Retrieving stored context:")
    print("=" * 50)

    # Get context for this user
    try:
        context = await zep.graph.search(
            user_id=user_id,
            query="Tell me about this person",
            scope="edges",
            limit=10
        )

        if context.edges:
            print(f"\nFacts/Edges ({len(context.edges)}):")
            for i, edge in enumerate(context.edges, 1):
                print(f"  {i}. {edge.fact}")
        else:
            print("\nNo edges found yet (facts may still be processing)")

    except Exception as e:
        print(f"Error retrieving context: {e}")

    # Also try getting user summary
    try:
        user_info = await zep.user.get(user_id)
        print(f"\nUser info: {user_info}")
    except Exception as e:
        print(f"Error getting user: {e}")

    print("\n" + "=" * 50)
    print("Test user setup complete!")
    print(f"Use user_id '{user_id}' when testing the proxy")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(setup_test_user())
