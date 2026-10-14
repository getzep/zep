"""Run a Google ADK agent with Zep v4 memory.

The automatic context hook adds retrieved content to a system instruction.
Use this example only with trusted, application-authored test content.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from zep_cloud import CustomInstruction, EntityProperty, EntityType, UserInstruction
from zep_cloud.client import AsyncZep

from zep_adk import (
    ZepContextTool,
    create_after_model_callback,
    create_thread,
    create_user,
)

APP_NAME = "zep-adk-example"
APP_USER_ID = "example-user"


async def send_message(runner: Runner, session_id: str, text: str) -> str:
    content = types.Content(role="user", parts=[types.Part(text=text)])
    response_parts: list[str] = []
    async for event in runner.run_async(
        user_id=APP_USER_ID,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            response_parts.extend(
                part.text for part in event.content.parts if hasattr(part, "text") and part.text
            )
    return " ".join(response_parts).strip()


async def configure_user(zep: AsyncZep, user_uuid: str, graph_uuid: str) -> None:
    """Apply example ontology and instructions after user creation."""
    company = EntityType(
        name="Company",
        description="A company or organization the user is associated with.",
        properties=[
            EntityProperty(
                name="industry",
                description="The company's industry",
                type="text",
            )
        ],
    )
    await zep.graph.set_ontology(graph_uuid, entity_types=[company])
    await zep.graph.set_instructions(
        graph_uuid,
        inherited=False,
        instructions=[
            CustomInstruction(
                name="project_context",
                text="Extract the user's role, team, and active projects.",
            )
        ],
    )
    await zep.user.set_summary_instructions(
        user_uuid,
        inherited=False,
        instructions=[
            UserInstruction(
                name="work_focus",
                text="Focus on the user's role, team, and active projects.",
            )
        ],
    )


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user = await create_user(
        zep,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
    )
    await configure_user(zep, user.uuid_, user.uuid_)
    thread = await create_thread(zep, user_uuid=user.uuid_)

    session_service = InMemorySessionService()
    session_id = f"session-{uuid4().hex}"
    state = {
        "zep_user_uuid": user.uuid_,
        "zep_thread_uuid": thread.uuid_,
        "zep_graph_uuid": user.uuid_,
        "zep_first_name": "Alice",
        "zep_last_name": "Smith",
    }
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=APP_USER_ID,
        session_id=session_id,
        state=state,
    )

    agent = Agent(
        name="zep_memory_agent",
        model="gemini-2.5-flash",
        instruction=(
            "You are a helpful assistant. Use the trusted context from Zep when it is relevant."
        ),
        tools=[ZepContextTool(zep_client=zep)],
        after_model_callback=create_after_model_callback(
            zep_client=zep,
            assistant_name="zep_memory_agent",
        ),
    )
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    # This fixed test content is authored by the application.
    for prompt in (
        "The example project is called Northstar.",
        "What is the example project called?",
    ):
        print(f"User: {prompt}")
        print(f"Agent: {await send_message(runner, session_id, prompt)}")


if __name__ == "__main__":
    asyncio.run(main())
