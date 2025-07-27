"""
Simple CrewAI + Zep integration example.

This example demonstrates the basic usage of ZepStorage with CrewAI's external memory system.
It shows how to manually save important information and let CrewAI automatically retrieve it
during agent execution.
"""

import os
import sys
import time
import uuid

from crewai import Agent, Crew, Process, Task
from crewai.memory.external.external_memory import ExternalMemory
from zep_cloud.client import Zep

from zep_crewai import ZepStorage


def main():
    # Check for API key
    api_key = os.environ.get("ZEP_API_KEY")
    if not api_key:
        print("❌ Error: Please set your ZEP_API_KEY environment variable")
        print("   Get your API key from: https://app.getzep.com")
        sys.exit(1)

    # Initialize Zep client
    zep_client = Zep(api_key=api_key)

    print("\n🤖 CrewAI + Zep Memory Integration Example")
    print("=" * 50)

    # Set up user and thread
    user_id = "demo_user_" + str(uuid.uuid4())
    thread_id = "demo_thread_" + str(uuid.uuid4())

    print(f"👤 User ID: {user_id}")
    print(f"🧵 Thread ID: {thread_id}")

    # Create user in Zep
    try:
        zep_client.user.add(
            user_id=user_id, first_name="John", last_name="Doe", email="john.doe@example.com"
        )
        print("✅ User created successfully")
    except Exception as e:
        if "already exists" in str(e).lower():
            print("✅ User already exists")
        else:
            print(f"⚠️  User creation issue: {e}")

    # Create thread
    try:
        zep_client.thread.create(user_id=user_id, thread_id=thread_id)
        print("✅ Thread created successfully")
    except Exception as e:
        print(f"⚠️  Thread creation issue: {e}")

    # Initialize Zep storage and external memory
    zep_storage = ZepStorage(client=zep_client, user_id=user_id, thread_id=thread_id)
    external_memory = ExternalMemory(storage=zep_storage)

    # Save conversation context and data (demonstrates metadata-based routing)
    print("\n💾 Saving conversation and business context to Zep...")

    # Save structured business trip data (goes to graph via json type)
    external_memory.save(
        '{"trip_type": "business", "destination": "New York", "duration": "3 days", "budget": 2000, "accommodation_preference": "mid-range hotels", "dietary_preference": "local cuisine"}',
        metadata={"type": "json"},
    )

    # Save user messages (go to thread via message type)
    external_memory.save(
        "Hi, I need help planning a business trip to New York. I'll be there for 3 days and prefer mid-range hotels.",
        metadata={"type": "message", "role": "user", "name": "John Doe"},
    )

    external_memory.save(
        "I'd be happy to help you plan your New York business trip! Let me find some great mid-range hotel options for you.",
        metadata={"type": "message", "role": "assistant", "name": "Travel Planning Assistant"},
    )

    # Save user preferences as text data (goes to graph via text type)
    external_memory.save(
        "John Doe prefers mid-range hotels with business amenities, enjoys trying local cuisine, and values convenient locations near business districts",
        metadata={"type": "text"},
    )

    external_memory.save(
        "Could you also recommend some good restaurants nearby? I love trying authentic local food when I travel for business.",
        metadata={"type": "message", "role": "user", "name": "John Doe"},
    )

    # Save budget constraint as text data (goes to graph)
    external_memory.save(
        "John Doe's budget constraint: Total budget is around $2000 for the entire trip including flights and accommodation. Looking for good value rather than luxury.",
        metadata={"type": "text"},
    )

    print("✅ Context saved to Zep memory")
    print("   • Messages → Thread API (conversation context)")
    print("   • JSON data → Graph API (structured trip info)")
    print("   • Text data → Graph API (preferences & constraints)")
    print(
        "   (Waiting 20 seconds until data is processed in zep)"
    )  # Give time for Zep to index the data
    time.sleep(20)

    # Create a simple agent
    travel_agent = Agent(
        role="Travel Planning Assistant",
        goal="Help plan business trips efficiently and within budget",
        backstory="""You are an experienced travel planner who specializes in business trips.
        You always consider the user's preferences, budget constraints, and trip context
        to provide practical recommendations.""",
        verbose=True,
        llm="gpt-4.1-mini",
    )

    # Create a simple task
    planning_task = Task(
        description="""Based on the user's saved preferences and context, provide 3 specific
        hotel recommendations in New York that would be good for a business traveler.

        Include:
        - Hotel names and locations
        - Price range per night
        - Why each hotel fits the user's preferences
        - Any special business amenities""",
        expected_output="A list of 3 hotel recommendations with detailed explanations",
        agent=travel_agent,
    )

    # Create crew with Zep memory
    crew = Crew(
        agents=[travel_agent],
        tasks=[planning_task],
        process=Process.sequential,
        external_memory=external_memory,  # This enables automatic memory retrieval during execution
        verbose=True,
    )

    print("\n🚀 Starting CrewAI execution...")
    print("   (The agent will automatically retrieve context from Zep memory)")

    # Execute the crew - CrewAI will automatically call zep_storage.search() to get context
    try:
        result = crew.kickoff()

        print("\n" + "=" * 60)
        print("RESULT:")
        print("=" * 60)
        print(result)
        print("=" * 60)

        # Optionally save the result back to memory for future use
        external_memory.save(str(result), metadata={"type": "message", "role": "assistant"})
        print("\n💾 Agent result saved to memory for future reference")

    except Exception as e:
        print(f"\n❌ Execution failed: {e}")
        return


if __name__ == "__main__":
    main()
