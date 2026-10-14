import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from zep_cloud import EntityProperty, EntityType, SearchFilters
from zep_cloud.client import AsyncZep

from zep_autogen import ZepGraphMemory

ENTITY_TYPES = [
    EntityType(
        name="ProgrammingLanguage",
        description="A programming language entity.",
        properties=[
            EntityProperty(
                name="paradigm",
                type="text",
                description="Programming paradigm, such as object-oriented or functional.",
            ),
            EntityProperty(
                name="use_case",
                type="text",
                description="Primary use cases for this language.",
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
                description="The programming language used by this framework.",
            ),
            EntityProperty(
                name="purpose",
                type="text",
                description="The primary purpose of this framework.",
            ),
        ],
    ),
]


async def main() -> None:
    zep_client = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    graph = await zep_client.graph.create(name="Programming Knowledge")
    graph_uuid = str(graph.uuid_)
    await zep_client.graph.set_ontology(graph_uuid, entity_types=ENTITY_TYPES)

    memory = ZepGraphMemory(
        client=zep_client,
        graph_uuid=graph_uuid,
        search_filters=SearchFilters(
            node_labels=["ProgrammingLanguage", "Framework"],
        ),
        max_characters=4000,
    )
    agent = AssistantAgent(
        name="GraphMemoryAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        memory=[memory],
    )

    for data in (
        "Python is a popular programming language for AI development.",
        "Machine learning models require large datasets for training.",
        "AutoGen is a framework for building multi-agent conversations.",
    ):
        await memory.add(
            MemoryContent(
                content=data,
                mime_type=MemoryMimeType.TEXT,
                metadata={"type": "data"},
            )
        )

    response = await agent.run(task="What technologies are useful for building AI agents?")
    print(response.messages[-1].content)

    results = await memory.query("Python frameworks", limit=5, scope="edges")
    for result in results.results:
        print(result.content)


if __name__ == "__main__":
    asyncio.run(main())
