"""
CrewAI + Zep User Storage Example.

This example demonstrates using ZepUserStorage for user-specific memories
and conversation threads.
"""

import os
import sys
import time
import uuid

from crewai import Agent, Crew, Process, Task
from zep_cloud.client import Zep

from zep_crewai import ZepUserStorage, create_search_tool


def main():
    # Check for API key
    api_key = os.environ.get("ZEP_API_KEY")
    if not api_key:
        print("❌ Error: Please set your ZEP_API_KEY environment variable")
        print("   Get your API key from: https://app.getzep.com")
        sys.exit(1)

    # Initialize Zep client
    zep_client = Zep(api_key=api_key)

    print("\n🤖 CrewAI + Zep User Storage Example")
    print("=" * 60)

    # Set up user and thread
    user_id = f"alice_{uuid.uuid4().hex[:8]}"
    thread_id = f"project_planning_{uuid.uuid4().hex[:8]}"

    print(f"👤 User ID: {user_id}")
    print(f"🧵 Thread ID: {thread_id}")

    # Create user in Zep
    print("\n👥 Setting up user profile...")
    try:
        zep_client.user.add(
            user_id=user_id,
            first_name="Alice",
            last_name="Johnson",
            email="alice.johnson@techcorp.com",
            metadata={
                "role": "Senior Product Manager",
                "department": "Product Development",
                "location": "San Francisco",
                "years_experience": 8,
            },
        )
        print("✅ User profile created")
    except Exception as e:
        if "already exists" in str(e).lower():
            print("✅ User already exists")
        else:
            print(f"⚠️  User creation issue: {e}")

    # Create thread for conversation
    try:
        zep_client.thread.create(user_id=user_id, thread_id=thread_id)
        print("✅ Conversation thread created")
    except Exception as e:
        print(f"⚠️  Thread creation issue: {e}")

    # Initialize user storage with thread
    user_storage = ZepUserStorage(
        client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
    )

    # Save user context and preferences
    print("\n💾 Saving user context and conversation history...")

    # Save conversation messages (go to thread)
    conversation_history = [
        {
            "content": "Hi team, I'd like to discuss our Q4 product roadmap. We need to prioritize features based on customer feedback.",
            "role": "user",
            "name": "Alice Johnson",
        },
        {
            "content": "Great idea, Alice. I've compiled the top customer requests from our support tickets.",
            "role": "assistant",
            "name": "Support Lead",
        },
        {
            "content": "Our main priorities should be: 1) Mobile app improvements, 2) API rate limiting, 3) Advanced analytics dashboard",
            "role": "user",
            "name": "Alice Johnson",
        },
        {
            "content": "I agree. The mobile app has been our most requested feature. We should allocate 40% of our resources there.",
            "role": "assistant",
            "name": "Engineering Lead",
        },
    ]

    for msg in conversation_history:
        user_storage.save(
            msg["content"], metadata={"type": "message", "role": msg["role"], "name": msg["name"]}
        )

    # Save user preferences and context (go to user graph)
    user_context = [
        {
            "data": '{"preference": "data-driven decision making", "communication_style": "direct and concise", "meeting_preference": "morning slots"}',
            "type": "json",
        },
        {
            "data": "Alice prefers using Slack for quick updates and email for formal documentation",
            "type": "text",
        },
        {
            "data": "Alice's team consists of 12 engineers, 3 designers, and 2 QA specialists",
            "type": "text",
        },
        {
            "data": '{"current_projects": ["Mobile App v2.0", "API Gateway", "Analytics Dashboard"], "budget": "$2M", "deadline": "Q4 2024"}',
            "type": "json",
        },
        {
            "data": "Alice has expertise in agile methodologies, particularly Scrum and Kanban",
            "type": "text",
        },
        {
            "data": "Previous successful launches: Customer Portal (Q1 2024), Integration Hub (Q2 2024)",
            "type": "text",
        },
    ]

    for item in user_context:
        user_storage.save(item["data"], metadata={"type": item["type"]})

    print("✅ User context saved")
    print("   • Conversation history → Thread API")
    print("   • User preferences → User Graph")
    print("   • Project information → User Graph")
    print("   (Waiting 20 seconds for data processing...)")
    time.sleep(20)

    # Give the agents a Zep search tool so they can recall Alice's context.
    search_tool = create_search_tool(zep_client, user_id=user_id)

    # Create specialized agents
    project_advisor = Agent(
        role="Project Management Advisor",
        goal="Provide personalized project management advice based on user's context and history",
        backstory="""You are an experienced project management consultant who helps
        product managers optimize their roadmaps and team resources. You understand
        the importance of considering past decisions, team dynamics, and user preferences.
        Use the Zep memory search tool to recall Alice's history and preferences.""",
        tools=[search_tool],
        verbose=True,
        llm="gpt-5-mini",
    )

    resource_planner = Agent(
        role="Resource Planning Specialist",
        goal="Optimize resource allocation based on project priorities and team capabilities",
        backstory="""You specialize in resource planning and allocation for software
        development teams. You consider team size, skills, budget, and deadlines
        when making recommendations. Use the Zep memory search tool to recall the
        team's composition and current projects.""",
        tools=[search_tool],
        verbose=True,
        llm="gpt-5-mini",
    )

    # Create tasks that leverage user-specific memory
    roadmap_task = Task(
        description="""Based on Alice's conversation history, preferences, and current projects,
        provide specific recommendations for the Q4 roadmap:
        1. Priority order for the three main features mentioned
        2. Resource allocation percentages for each feature
        3. Risk factors based on team size and deadline
        4. Communication plan aligned with Alice's preferences""",
        expected_output="A detailed Q4 roadmap with specific recommendations",
        agent=project_advisor,
    )

    resource_task = Task(
        description="""Using the information about Alice's team and budget, create a
        resource allocation plan that:
        1. Assigns team members to each priority feature
        2. Identifies any skill gaps that need to be addressed
        3. Provides a timeline that fits within Q4 2024
        4. Suggests contingency plans for high-risk items""",
        expected_output="A comprehensive resource allocation plan with timeline",
        agent=resource_planner,
    )

    # Create crew
    crew = Crew(
        agents=[project_advisor, resource_planner],
        tasks=[roadmap_task, resource_task],
        process=Process.sequential,
        verbose=True,
    )

    print("\n🚀 Starting CrewAI execution with user-specific memory...")
    print("   (Agents will access Alice's context and conversation history)")

    try:
        result = crew.kickoff()

        print("\n" + "=" * 60)
        print("PERSONALIZED RECOMMENDATIONS:")
        print("=" * 60)
        print(result)
        print("=" * 60)

        # Save recommendations back to user's memory
        user_storage.save(
            str(result), metadata={"type": "message", "role": "assistant", "name": "Planning Crew"}
        )
        print("\n💾 Recommendations saved to Alice's memory for future reference")

        # Demonstrate memory search
        print("\n🔍 Searching Alice's memories...")
        search_results = user_storage.search("mobile app", limit=5)
        print(f"Found {len(search_results)} relevant memories about mobile app:")
        for idx, result in enumerate(search_results[:3], 1):
            memory_type = result.get("type", "unknown")
            content = result.get("context", "")[:100]
            print(f"  {idx}. [{memory_type}] {content}...")

    except Exception as e:
        print(f"\n❌ Execution failed: {e}")
        return


if __name__ == "__main__":
    main()
