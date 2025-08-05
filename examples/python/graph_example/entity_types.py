import asyncio
import os
import uuid
from dotenv import find_dotenv, load_dotenv

from zep_cloud import EntityEdgeSourceTarget, SearchFilters, Message
from zep_cloud.client import AsyncZep
from pydantic import Field
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

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
    await client.graph.set_entity_types(
        entities={
            "Destination": Destination,
        },
        edges={
            "TRAVELING_TO": (
                TravelingTo,
                [
                    EntityEdgeSourceTarget(
                        source="User",
                        target="Destination"
                    )
                ]
            ),
        }
    )

    messages = [
        Message(content="I'm planning to visit Tokyo, Japan next month for a business trip. Tokyo is in the Kanto region and it's such a vibrant metropolitan city with amazing technology and culture.", role="user", name="John Doe"),
        Message(content="That sounds like an exciting business trip! Tokyo is indeed a fascinating destination. When are you planning to travel there exactly?", role="assistant", name="Assistant"),
        Message(content="I'll be traveling to Tokyo on March 15th, 2024 for business meetings. After that, I'm thinking of taking a leisure trip to Bali, Indonesia in April. Bali is in the Lesser Sunda Islands region and is known for its beautiful beaches and temples.", role="user", name="John Doe"),
        Message(content="Great planning! Tokyo for business in March and then Bali for leisure in April - that's a nice combination of work and relaxation.", role="assistant", name="Assistant"),
    ]

    user_id = f"user-{uuid.uuid4()}"
    await client.user.add(user_id=user_id, first_name="John", last_name="Doe", email="john.doe@example.com")
    thread_id = f"thread-{uuid.uuid4()}"
    await client.thread.create(thread_id=thread_id, user_id=user_id)

    await client.thread.add_messages(
        thread_id=thread_id,
        messages=messages,
    )

    # Wait for the graph to process the messages
    print("Waiting for graph processing...")
    await asyncio.sleep(10)

    results = await client.graph.search(
        user_id=user_id,
        query="travel",
        # scope="nodes",
        scope="edges",
        search_filters=SearchFilters(
            edge_types=["TRAVELING_TO"]
            # node_labels=["Destination"]
        )
    )

    if results.nodes:
        for node in results.nodes:
            print(Destination(**node.attributes))
    if results.edges:
        for edge in results.edges:
            print(TravelingTo(**edge.attributes))


    enntl = await client.graph.list_entity_types()
    print(enntl)

if __name__ == "__main__":
    asyncio.run(main())