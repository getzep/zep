import asyncio
import os
import time
import uuid
from dotenv import find_dotenv, load_dotenv

from zep_cloud import Message
from zep_cloud.client import AsyncZep
from zep_cloud.ontology import EdgeModel, EntityModel, EntityText, build_ontology
from pydantic import Field
from zep_cloud.types import EdgeSearchFilters, EdgeSourceTarget

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

    messages = [
        Message(content="I'm planning to visit Tokyo, Japan next month for a business trip. Tokyo is in the Kanto region and it's such a vibrant metropolitan city with amazing technology and culture.", role="user", name="John Doe"),
        Message(content="That sounds like an exciting business trip! Tokyo is indeed a fascinating destination. When are you planning to travel there exactly?", role="assistant", name="Assistant"),
        Message(content="I'll be traveling to Tokyo on March 15th, 2024 for business meetings. After that, I'm thinking of taking a leisure trip to Bali, Indonesia in April. Bali is in the Lesser Sunda Islands region and is known for its beautiful beaches and temples.", role="user", name="John Doe"),
        Message(content="Great planning! Tokyo for business in March and then Bali for leisure in April - that's a nice combination of work and relaxation.", role="assistant", name="Assistant"),
    ]

    user = await client.user.create(
        user_id=f"examples-v4-ontology-{uuid.uuid4().hex}",
        first_name="John",
        last_name="Doe",
        email="john.doe@example.com",
    )
    user_uuid = user.uuid_
    await client.graph.set_ontology(
        user_uuid, entity_types=entity_types, edge_types=edge_types
    )
    thread = await client.thread.create(user_uuid=user_uuid)
    thread_uuid = thread.uuid_

    added = await client.thread.add_messages(
        thread_uuid=thread_uuid,
        messages=messages,
    )

    if added.task and added.task.uuid_:
        print("Waiting for graph processing...")
        deadline = time.monotonic() + 1200
        next_status_report = time.monotonic() + 30
        while True:
            task = await client.task.get(added.task.uuid_)
            if task.status == "succeeded":
                break
            if task.status == "partial":
                print(f"Warning: task {added.task.uuid_} ended with partial status: {task.error}")
                break
            if task.status == "failed":
                raise RuntimeError(f"Message ingestion task ended with failed status: {task.error}")
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("Timed out waiting for message ingestion")
            if now >= next_status_report:
                print(f"Task {added.task.uuid_} status={task.status}; still waiting.")
                next_status_report = now + 30
            await asyncio.sleep(2)

    results = await client.graph.search_edges(
        user_uuid,
        query="travel",
        page_size=10,
        filters=EdgeSearchFilters(edge_types=["TRAVELING_TO"]),
    )
    for edge in results.items:
        print(TravelingTo(**(edge.attributes or {})))
    print(await client.graph.get_ontology(user_uuid))

if __name__ == "__main__":
    asyncio.run(main())