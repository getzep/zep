import os

from dotenv import load_dotenv
from livekit import agents
from livekit.plugins import openai, silero
from pydantic import Field
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.ontology import EdgeModel, EntityModel, EntityText, build_ontology
from zep_cloud.types import EdgeSourceTarget

from zep_livekit import ZepGraphAgent


class Restaurant(EntityModel):
    """Represents a specific restaurant."""

    cuisine_type: EntityText = Field(
        description=("The cuisine type of the restaurant, for example: American, Mexican, Indian."),
        default=None,
    )
    dietary_accommodation: EntityText = Field(
        description=(
            "The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan."
        ),
        default=None,
    )


class RestaurantVisit(EdgeModel):
    """Represents the fact that a person visited a restaurant."""

    restaurant_name: EntityText = Field(
        description="The name of the restaurant that the person visited.",
        default=None,
    )


entity_types, edge_types = build_ontology(
    entities={"Restaurant": Restaurant},
    edges={
        "RESTAURANT_VISIT": (
            RestaurantVisit,
            [EdgeSourceTarget(source="User", target="Restaurant")],
        )
    },
)

# Load environment variables
load_dotenv()

# Constants. Zep v4 addresses a graph by a UUID. Set ZEP_GRAPH_UUID to reuse
# a graph that exists. If it is empty, the example creates a graph and prints
# the UUID for later runs.
GRAPH_UUID = os.getenv("ZEP_GRAPH_UUID", "")
GRAPH_NAME = "Travel Knowledge Graph"
USER_NAME = "John"


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent job."""

    # Step 1: Initialize Zep client
    zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Step 2: Get the graph by UUID, or create the graph one time
    if GRAPH_UUID:
        graph = await zep_client.graph.get(GRAPH_UUID)
    else:
        graph = await zep_client.graph.create(name=GRAPH_NAME)
        print(f"Created Zep graph. Set ZEP_GRAPH_UUID={graph.uuid_} for the next run.")
    graph_uuid = graph.uuid_ or ""

    # Step 3: Set custom ontology for the graph (if needed)
    await zep_client.graph.set_ontology(
        graph_uuid,
        entity_types=entity_types,
        edge_types=edge_types,
    )

    # Step 4: Connect to LiveKit room
    await ctx.connect()

    # Step 5: Create agent session with OpenAI components
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-5-nano-2025-08-07"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )

    # Step 6: Configure search filters to use Location entity type (relevant for travel)
    search_filters = SearchFilters(
        node_labels=["Restaurant"],
        edge_labels=["RESTAURANT_VISIT"],
    )

    # Step 7: Create the graph memory-enabled agent with all possible arguments
    agent = ZepGraphAgent(
        zep_client=zep_client,
        graph_uuid=graph_uuid,
        user_name=USER_NAME,
        max_characters=4000,
        search_filters=search_filters,
        instructions="""You are a helpful assistant who responds concisely in at most 1 sentence for each response. If the user asks you to complete a task of any kind, such as playing music or using any other kind of tool, pretend that you can in fact do that task for simulation purposes.""",
    )

    # Step 8: Start the session
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    # Validate environment variables
    required_vars = [
        "OPENAI_API_KEY",
        "ZEP_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"❌ Missing required environment variables: {missing_vars}")
        exit(1)

    # Start the LiveKit agent
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
