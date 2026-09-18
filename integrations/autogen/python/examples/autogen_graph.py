import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud import EntityProperty, EntityType, SearchFilters
from zep_cloud.client import AsyncZep

from zep_autogen.graph_memory import ZepGraphMemory

ENTITY_TYPES = [
    EntityType(
        name="ProgrammingLanguage",
        description="A programming language entity.",
        properties=[
            EntityProperty(
                name="paradigm",
                type="text",
                description="programming paradigm (e.g., object-oriented, functional)",
            ),
            EntityProperty(
                name="use_case",
                type="text",
                description="primary use cases for this language",
            ),
        ],
    ),
    EntityType(
        name="Framework",
        description="A software framework or library.",
        properties=[
            EntityProperty(
                name="language",
                type="text",
                description="the programming language this framework is built for",
            ),
            EntityProperty(
                name="purpose",
                type="text",
                description="primary purpose of this framework",
            ),
        ],
    ),
    EntityType(
        name="Concept",
        description="A programming concept or technique.",
        properties=[
            EntityProperty(
                name="category",
                type="text",
                description="category of concept (e.g., design pattern, algorithm)",
            ),
            EntityProperty(
                name="difficulty",
                type="text",
                description="difficulty level (beginner, intermediate, advanced)",
            ),
        ],
    ),
]


async def main():
    # Initialize AsyncZep client
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    # Zep assigns the UUID of the graph. Keep this UUID in your own database.
    graph = await zep_client.graph.create(name="Knowledge Graph")
    graph_uuid = str(graph.uuid_)
    print(f"Created graph: {graph_uuid}")

    # The ontology applies to one graph UUID in v4.
    await zep_client.graph.set_ontology(graph_uuid, entity_types=ENTITY_TYPES)

    # Initialize Zep graph memory bound to the assistant
    memory = ZepGraphMemory(
        client=zep_client,
        graph_uuid=graph_uuid,
        search_filters=SearchFilters(
            node_labels=["ProgrammingLanguage", "Framework", "Concept"],
        ),
    )

    # Create assistant agent with Zep graph memory
    agent = AssistantAgent(
        name="GraphMemoryAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        memory=[memory],
    )

    # Helper function to store data in graph memory
    async def add_data(data: str, data_type: str = "data"):
        """Store data in graph memory"""
        metadata = {"type": data_type}

        await memory.add(
            MemoryContent(content=data, mime_type=MemoryMimeType.TEXT, metadata=metadata)
        )

    # Example conversation with graph memory storage
    try:
        print("\n=== Starting conversation with graph memory ===")

        # Store some facts and knowledge
        await add_data("Python is a popular programming language for AI development")
        await add_data("Machine learning models require large datasets for training")
        await add_data("AutoGen is a framework for building multi-agent conversations")
        await add_data("Graph databases are useful for storing connected information")
        print("Stored knowledge in graph memory")
        await asyncio.sleep(50)  # Wait for graph processing
        # Store some episode/message data
        user_msg1 = "Tell me about Python and machine learning."
        print(f"\nUser: {user_msg1}")
        await memory.add(
            MemoryContent(
                content=user_msg1, mime_type=MemoryMimeType.TEXT, metadata={"type": "message"}
            )
        )

        response1 = await agent.run(task=user_msg1)
        agent_msg1 = response1.messages[-1].content
        print(f"Agent: {agent_msg1}")

        await memory.add(
            MemoryContent(
                content=agent_msg1, mime_type=MemoryMimeType.TEXT, metadata={"type": "message"}
            )
        )

        # Second interaction - agent should use graph context
        user_msg2 = "What can you tell me about building AI agents?"
        print(f"\nUser: {user_msg2}")
        await memory.add(
            MemoryContent(
                content=user_msg2, mime_type=MemoryMimeType.TEXT, metadata={"type": "message"}
            )
        )

        response2 = await agent.run(task=user_msg2)
        agent_msg2 = response2.messages[-1].content
        print(f"Agent: {agent_msg2}")

        await memory.add(
            MemoryContent(
                content=agent_msg2, mime_type=MemoryMimeType.TEXT, metadata={"type": "message"}
            )
        )

        # Test querying graph memory directly
        print("\n=== Querying graph memory directly ===")
        query_result = await memory.query("What technologies are useful for AI?", limit=5)
        print(f"Query results ({len(query_result.results)} items):")
        for i, result in enumerate(query_result.results[:3], 1):
            print(f"{i}. {result.content[:100]}...")

        print("\n=== Graph memory demonstration complete ===")

    except Exception as e:
        print(f"Error during conversation: {e}")


if __name__ == "__main__":
    asyncio.run(main())
