"""
Set up a test user in Zep with some facts/memories.

Run this first to populate Zep with data that the proxy can retrieve.
"""

import asyncio
import os
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud import AddMessage

load_dotenv()


async def setup_test_user():
    """Create a test user and thread with some memories in Zep."""

    zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    print("Setting up test user")
    print("=" * 50)

    # 1. Create the user. v4 gives every user a server-generated UUID, so a
    # create call does not send a user_id.
    print("Creating new user...")
    user = await zep.user.create(
        first_name="Randy",
        last_name="Adams",
        email="randy@talk2me.example.com",
        metadata={
            "company": "Talk2Me",
            "role": "Founder",
            "interests": ["AI", "voice technology", "celebrities"]
        }
    )
    user_uuid = user.uuid_
    print(f"Created user: {user_uuid}")

    # 2. Create a thread for this user
    print("Creating new thread...")
    thread = await zep.thread.create(
        user_uuid=user_uuid
    )
    thread_uuid = thread.uuid_
    print(f"Created thread: {thread_uuid}")

    # 3. Add some conversation history that will generate facts
    print("\nAdding conversation history...")

    messages = [
        AddMessage(
            role="user",
            content="Hi, I'm Randy. I run a company called Talk2Me where we build voice AI agents for celebrities."
        ),
        AddMessage(
            role="assistant",
            content="Nice to meet you, Randy! Talk2Me sounds fascinating. Building voice AI for celebrities must involve some interesting challenges with authenticity and personality matching."
        ),
        AddMessage(
            role="user",
            content="Yes, we work with Tim Draper, Kelsey Plum, and several others. We're launching four new celebrity voices in February."
        ),
        AddMessage(
            role="assistant",
            content="That's impressive! Tim Draper and Kelsey Plum are quite different personalities. The February launch sounds exciting - four new voices is ambitious but shows great momentum."
        ),
        AddMessage(
            role="user",
            content="I used to work at NeXT with Steve Jobs. Back when there were only 11 people at the company."
        ),
        AddMessage(
            role="assistant",
            content="Wow, that's incredible history! Working alongside Steve Jobs in the early NeXT days must have been an extraordinary experience. That perspective probably gives you unique insights into building innovative technology products."
        ),
        AddMessage(
            role="user",
            content="We're concerned about latency in our voice agents. Two seconds is too long for a response."
        ),
        AddMessage(
            role="assistant",
            content="Latency is critical for voice - two seconds definitely breaks the natural flow of conversation. Most successful voice AI aims for under 500ms response time. Are you seeing the delay in the LLM, TTS, or somewhere else in the pipeline?"
        ),
    ]

    try:
        await zep.thread.add_messages(thread_uuid, messages=messages)
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
        edges = []
        async for edge in await zep.graph.search_edges(
            user.graph_uuid,
            query="Tell me about this person",
            limit=10
        ):
            edges.append(edge)

        if edges:
            print(f"\nFacts/Edges ({len(edges)}):")
            for i, edge in enumerate(edges, 1):
                print(f"  {i}. {edge.fact}")
        else:
            print("\nNo edges found yet (facts may still be processing)")

    except Exception as e:
        print(f"Error retrieving context: {e}")

    # Also try getting user summary
    try:
        user_info = await zep.user.get(user_uuid)
        print(f"\nUser info: {user_info}")
    except Exception as e:
        print(f"Error getting user: {e}")

    print("\n" + "=" * 50)
    print("Test user setup complete!")
    print(f"Use the user UUID '{user_uuid}' when testing the proxy")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(setup_test_user())
