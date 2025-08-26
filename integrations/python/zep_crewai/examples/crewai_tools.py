"""
CrewAI + Zep Tools Example.

This example demonstrates using Zep tools with CrewAI agents for
searching and adding data to both user and graph storage.
"""

import os
import sys
import time
import uuid

from crewai import Agent, Crew, Process, Task
from zep_cloud.client import Zep

from zep_crewai import create_add_data_tool, create_search_tool


def main():
    # Check for API key
    api_key = os.environ.get("ZEP_API_KEY")
    if not api_key:
        print("❌ Error: Please set your ZEP_API_KEY environment variable")
        print("   Get your API key from: https://app.getzep.com")
        sys.exit(1)

    # Initialize Zep client
    zep_client = Zep(api_key=api_key)

    print("\n🤖 CrewAI + Zep Tools Example")
    print("=" * 60)

    # Set up both user and graph storage
    user_id = f"bob_{uuid.uuid4().hex[:8]}"
    graph_id = f"company_knowledge_{uuid.uuid4().hex[:8]}"

    print(f"👤 User ID: {user_id}")
    print(f"📊 Graph ID: {graph_id}")

    zep_client.user.add(
        user_id=user_id,
        first_name="Bob",
        last_name="Smith",
        email="bob.smith@example.com",
    )
    print("✅ User created")

    zep_client.graph.create(
        graph_id=graph_id,
    )
    print("✅ Graph created")

    # Create tools for user-specific storage
    user_search_tool = create_search_tool(zep_client, user_id=user_id)
    user_add_tool = create_add_data_tool(zep_client, user_id=user_id)

    # Create tools for graph storage
    graph_search_tool = create_search_tool(zep_client, graph_id=graph_id)
    graph_add_tool = create_add_data_tool(zep_client, graph_id=graph_id)

    print("✅ Tools created:")
    print(f"   • User tools: {user_search_tool.name}, {user_add_tool.name}")
    print(f"   • Graph tools: {graph_search_tool.name}, {graph_add_tool.name}")

    # Create specialized agents with different tool sets
    personal_assistant = Agent(
        role="Personal Knowledge Assistant",
        goal="Manage and retrieve Bob's personal work information and preferences",
        backstory="""You are Bob's personal assistant, helping him track his projects,
        preferences, and work-related information. You have access to Bob's personal
        knowledge base where you can store and retrieve information specific to him.""",
        tools=[user_search_tool, user_add_tool],
        verbose=True,
        llm="gpt-4o-mini",
    )

    knowledge_curator = Agent(
        role="Company Knowledge Curator",
        goal="Maintain and query the company's shared knowledge base",
        backstory="""You manage the company's shared knowledge base, storing important
        information about processes, best practices, and organizational knowledge that
        benefits everyone.""",
        tools=[graph_search_tool, graph_add_tool],
        verbose=True,
        llm="gpt-4o-mini",
    )

    research_analyst = Agent(
        role="Research Analyst",
        goal="Analyze information from both personal and company knowledge bases",
        backstory="""You are a research analyst who can access both personal and company
        knowledge to provide comprehensive insights and recommendations.""",
        tools=[user_search_tool, graph_search_tool],  # Read-only access to both
        verbose=True,
        llm="gpt-4o-mini",
    )

    # Task 1: Store Bob's personal information
    personal_setup_task = Task(
        description="""Store the following information about Bob in his personal knowledge base:
        1. Current project: "Customer Churn Prediction Model" using XGBoost
        2. Preferred working hours: 9 AM - 5 PM Pacific Time
        3. Current learning goal: Deep learning with PyTorch
        4. Team members: Alice (PM), Charlie (Engineer), Diana (Designer)
        5. Upcoming deadline: Model deployment by end of month

        Use the add data tool to store each piece of information appropriately.""",
        expected_output="Confirmation that all personal information has been stored",
        agent=personal_assistant,
    )

    # Task 2: Store company knowledge
    company_setup_task = Task(
        description="""Add the following company information to the shared knowledge base:
        1. Company mission: "Empower businesses with data-driven insights"
        2. Core values: Innovation, Collaboration, Customer Focus
        3. Data science best practices: Always version control models, document assumptions, peer review code
        4. Standard tech stack: Python, Snowflake, Tableau, Git
        5. Meeting protocol: All meetings require agenda and action items

        Store this as structured data in the company knowledge graph.""",
        expected_output="Confirmation that company information has been added to the knowledge base",
        agent=knowledge_curator,
    )

    # Wait for data to be indexed
    setup_crew = Crew(
        agents=[personal_assistant, knowledge_curator],
        tasks=[personal_setup_task, company_setup_task],
        process=Process.sequential,
        verbose=True,
    )

    print("\n📝 Phase 1: Storing information using tools...")
    try:
        setup_result = setup_crew.kickoff()
        print(f"\nSetup completed: {setup_result}")
    except Exception as e:
        print(f"Setup failed: {e}")
        return

    print("\n⏳ Waiting 20 seconds for data processing...")
    time.sleep(20)

    # Task 3: Search and analyze
    search_task = Task(
        description="""Perform the following searches and provide a summary:
        1. Search Bob's personal knowledge for his current project and team
        2. Search the company knowledge base for best practices
        3. Based on both searches, provide recommendations for Bob's project

        Make sure to clearly indicate which information comes from personal vs company knowledge.""",
        expected_output="A comprehensive analysis combining personal and company information",
        agent=research_analyst,
    )

    # Task 4: Enrichment task
    enrichment_task = Task(
        description="""Based on the research analyst's findings:
        1. Add a note to Bob's personal knowledge about recommended next steps
        2. Search for any gaps in Bob's knowledge that could help with his project
        3. Store any new insights discovered during the analysis

        This demonstrates the full cycle of search, analyze, and store.""",
        expected_output="Summary of new insights added to Bob's knowledge base",
        agent=personal_assistant,
    )

    # Create analysis crew
    analysis_crew = Crew(
        agents=[research_analyst, personal_assistant],
        tasks=[search_task, enrichment_task],
        process=Process.sequential,
        verbose=True,
    )

    print("\n🔍 Phase 2: Searching and analyzing with tools...")
    try:
        analysis_result = analysis_crew.kickoff()

        print("\n" + "=" * 60)
        print("ANALYSIS RESULTS:")
        print("=" * 60)
        print(analysis_result)
        print("=" * 60)

        # Demonstrate direct tool usage
        print("\n🔧 Direct tool demonstration...")

        print("\n1. Searching Bob's personal knowledge:")
        personal_results = user_search_tool._run("project", limit=3)
        print(personal_results)

        print("\n2. Searching company knowledge:")
        company_results = graph_search_tool._run("best practices", limit=3, scope="all")
        print(company_results)

    except Exception as e:
        print(f"\n❌ Analysis failed: {e}")
        return


if __name__ == "__main__":
    main()
