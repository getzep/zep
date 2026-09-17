"""
CrewAI + Zep Graph Storage Example with Ontology.

This example demonstrates using ZepGraphStorage with ontology definitions
for structured knowledge organization.
"""

import os
import sys
import time

from crewai import Agent, Crew, Process, Task
from zep_cloud import SearchFilters
from zep_cloud.client import Zep
from zep_cloud.types import EntityProperty, EntityType

from zep_crewai import ZepGraphStorage, create_search_tool

# Zep v4 declares an ontology with the typed EntityType model. The v3
# EntityModel/EntityText classes are not in the v4 SDK.
TECHNOLOGY_ENTITY = EntityType(
    name="Technology",
    description="a technology, such as a language, a framework, or a tool",
    properties=[
        EntityProperty(
            name="category",
            type="text",
            description="technology category (for example programming, framework, tool)",
        ),
        EntityProperty(name="use_case", type="text", description="primary use cases"),
        EntityProperty(name="difficulty", type="text", description="learning difficulty level"),
    ],
)

COMPANY_ENTITY = EntityType(
    name="Company",
    description="a company that uses technologies",
    properties=[
        EntityProperty(name="industry", type="text", description="company industry"),
        EntityProperty(
            name="size",
            type="text",
            description="company size (startup, mid-size, enterprise)",
        ),
        EntityProperty(
            name="tech_stack", type="text", description="technologies that the company uses"
        ),
    ],
)


def main():
    # Check for API key
    api_key = os.environ.get("ZEP_API_KEY")
    if not api_key:
        print("❌ Error: Please set your ZEP_API_KEY environment variable")
        print("   Get your API key from: https://app.getzep.com")
        sys.exit(1)

    # Initialize Zep client
    zep_client = Zep(api_key=api_key)

    print("\n🤖 CrewAI + Zep Graph Storage with Ontology Example")
    print("=" * 60)

    # Create the graph. Zep v4 gives the graph a server-generated UUID, and
    # the application stores that UUID.
    graph = zep_client.graph.create(name="tech knowledge")
    graph_uuid = graph.uuid_ or ""
    print(f"📊 Graph UUID: {graph_uuid}")

    # Set up ontology for the graph
    print("\n📚 Setting up graph ontology...")
    try:
        zep_client.graph.set_ontology(
            graph_uuid,
            entity_types=[TECHNOLOGY_ENTITY, COMPANY_ENTITY],
        )
        print("✅ Ontology configured with Technology and Company entities")
    except Exception as e:
        print(f"⚠️  Ontology setup issue: {e}")

    # Initialize Zep graph storage
    graph_storage = ZepGraphStorage(
        client=zep_client,
        graph_uuid=graph_uuid,
        search_filters=SearchFilters(node_labels=["Technology", "Company"]),
    )

    # Save structured knowledge to the graph
    print("\n💾 Saving technology knowledge to graph...")

    # Add technology information
    tech_data = [
        {
            "data": '{"name": "Python", "category": "programming language", "use_case": "AI/ML, web development, data science", "difficulty": "beginner-friendly"}',
            "type": "json",
        },
        {
            "data": '{"name": "React", "category": "frontend framework", "use_case": "building interactive UIs, single-page applications", "difficulty": "moderate"}',
            "type": "json",
        },
        {
            "data": '{"name": "Docker", "category": "containerization tool", "use_case": "application deployment, microservices", "difficulty": "moderate to advanced"}',
            "type": "json",
        },
        {
            "data": "Python is widely used at tech companies for machine learning and backend development",
            "type": "text",
        },
        {
            "data": "React powers many modern web applications and is maintained by Meta",
            "type": "text",
        },
    ]

    for item in tech_data:
        graph_storage.save(item["data"], metadata={"type": item["type"]})

    # Add company information
    company_data = [
        {
            "data": '{"name": "TechCorp", "industry": "Software", "size": "enterprise", "tech_stack": "Python, React, Docker, Kubernetes"}',
            "type": "json",
        },
        {
            "data": '{"name": "StartupAI", "industry": "Artificial Intelligence", "size": "startup", "tech_stack": "Python, PyTorch, FastAPI"}',
            "type": "json",
        },
        {
            "data": "TechCorp recently migrated their entire infrastructure to Kubernetes for better scalability",
            "type": "text",
        },
        {
            "data": "StartupAI focuses on developing cutting-edge NLP models using transformer architectures",
            "type": "text",
        },
    ]

    for item in company_data:
        graph_storage.save(item["data"], metadata={"type": item["type"]})

    print("✅ Knowledge saved to graph")
    print("   • Technology entities with categories and use cases")
    print("   • Company entities with industry and tech stack")
    print("   • Relationships and facts about technologies and companies")
    print("   (Waiting 20 seconds for data processing...)")
    time.sleep(20)

    # Give the agents a Zep search tool bound to the knowledge graph.
    search_tool = create_search_tool(zep_client, graph_uuid=graph_uuid)

    # Create specialized agents
    tech_analyst = Agent(
        role="Technology Analyst",
        goal="Analyze technology trends and provide insights about tech stacks",
        backstory="""You are an experienced technology analyst who understands
        various programming languages, frameworks, and tools. You help companies
        make informed decisions about their technology choices. Use the Zep memory
        search tool to query the knowledge graph for technology and company facts.""",
        tools=[search_tool],
        verbose=True,
        llm="gpt-5-mini",
    )

    recruiter = Agent(
        role="Technical Recruiter",
        goal="Match technology skills with company requirements",
        backstory="""You are a technical recruiter who understands both
        technology requirements and company needs. You help identify the right
        skills for different roles and companies. Use the Zep memory search tool
        to query the knowledge graph.""",
        tools=[search_tool],
        verbose=True,
        llm="gpt-5-mini",
    )

    # Create tasks that leverage the graph knowledge
    analysis_task = Task(
        description="""Based on the stored knowledge about technologies and companies,
        provide an analysis of:
        1. Which technologies are most commonly used across companies
        2. The relationship between company size and technology choices
        3. Recommendations for a new startup choosing a tech stack""",
        expected_output="A detailed analysis with specific technology recommendations",
        agent=tech_analyst,
    )

    recruitment_task = Task(
        description="""Based on the technology and company information available,
        create a skills matrix that shows:
        1. Essential skills for different company types
        2. Technology combinations that are commonly used together
        3. Difficulty levels for learning different technologies""",
        expected_output="A structured skills matrix with learning paths",
        agent=recruiter,
    )

    # Create crew
    crew = Crew(
        agents=[tech_analyst, recruiter],
        tasks=[analysis_task, recruitment_task],
        process=Process.sequential,
        verbose=True,
    )

    print("\n🚀 Starting CrewAI execution with graph memory...")
    print("   (Agents will query the knowledge graph via their search tool)")

    try:
        result = crew.kickoff()

        print("\n" + "=" * 60)
        print("ANALYSIS RESULTS:")
        print("=" * 60)
        print(result)
        print("=" * 60)

        # Save analysis results back to the graph
        graph_storage.save(f"Analysis completed: {str(result)[:500]}", metadata={"type": "text"})
        print("\n💾 Analysis results saved to graph for future reference")

        # Demonstrate direct graph search
        print("\n🔍 Demonstrating direct graph search...")
        search_results = graph_storage.search("Python", limit=5)
        print(f"Found {len(search_results)} results about Python:")
        for idx, result in enumerate(search_results[:3], 1):
            print(f"  {idx}. {result.get('context', '')[:100]}...")

    except Exception as e:
        print(f"\n❌ Execution failed: {e}")
        return


if __name__ == "__main__":
    main()
