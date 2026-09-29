import asyncio
import os
from dotenv import find_dotenv, load_dotenv

from zep_cloud import AddMessage, EdgeSourceTarget, SearchFilters
from zep_cloud.client import AsyncZep
from pydantic import Field
from zep_cloud.ontology import EntityModel, EntityText, EdgeModel, build_ontology

load_dotenv(
    dotenv_path=find_dotenv()
)

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(
        api_key=API_KEY,
    )

    class Destination(EntityModel):
        """
        A destination is a place that travelers visit.
        """
        destination_name: EntityText = Field(
            description="The name of the destination",
            default=None
        )
        country: EntityText = Field(
            description="The country of the destination",
            default=None
        )
        region: EntityText = Field(
            description="The region of the destination",
            default=None
        )
        description: EntityText = Field(
            description="A description of the destination",
            default=None
        )

    class TravelingTo(EdgeModel):
        """
        An edge representing a traveler going to a destination.
        """
        travel_date: EntityText = Field(
            description="The date of travel to this destination",
            default=None
        )
        purpose: EntityText = Field(
            description="The purpose of travel (Business, Leisure, etc.)",
            default=None
        )
    entity_types, edge_types = build_ontology(
        entities={
            "Destination": Destination,
        },
        edges={
            "TRAVELING_TO": (
                TravelingTo,
                [
                    EdgeSourceTarget(
                        source="User",
                        target="Destination"
                    )
                ]
            ),
        }
    )
    await client.project.set_ontology(entity_types=entity_types, edge_types=edge_types)

    messages = [
        AddMessage(content="I'm planning to visit Tokyo, Japan next month for a business trip. Tokyo is in the Kanto region and it's such a vibrant metropolitan city with amazing technology and culture.", role="user", name="John Doe"),
        AddMessage(content="That sounds like an exciting business trip! Tokyo is indeed a fascinating destination. When are you planning to travel there exactly?", role="assistant", name="Assistant"),
        AddMessage(content="I'll be traveling to Tokyo on March 15th, 2024 for business meetings. After that, I'm thinking of taking a leisure trip to Bali, Indonesia in April. Bali is in the Lesser Sunda Islands region and is known for its beautiful beaches and temples.", role="user", name="John Doe"),
        AddMessage(content="Great planning! Tokyo for business in March and then Bali for leisure in April - that's a nice combination of work and relaxation.", role="assistant", name="Assistant"),
    ]

    user = await client.user.create(first_name="John", last_name="Doe", email="john.doe@example.com")
    thread = await client.thread.create(user_uuid=user.uuid_)

    await client.thread.add_messages(
        thread.uuid_,
        messages=messages,
    )

    # Wait for the graph to process the messages
    print("Waiting for graph processing...")
    await asyncio.sleep(10)

    edges = [
        edge
        async for edge in await client.graph.search_edges(
            user.graph_uuid,
            query="travel",
            filters=SearchFilters(
                edge_types=["TRAVELING_TO"]
            ),
        )
    ]

    for edge in edges:
        if edge.attributes:
            print(TravelingTo(**edge.attributes))

    ontology = await client.project.get_ontology()
    print(ontology)

if __name__ == "__main__":
    asyncio.run(main())