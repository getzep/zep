"""Live tests for the Google ADK integration and Zep v4 SDK."""

from __future__ import annotations

import os
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from zep_cloud.client import AsyncZep

from zep_adk import (
    ZepContextTool,
    ZepGraphSearchTool,
    ZepMemoryService,
    create_after_model_callback,
    create_thread,
    create_user,
)

ZEP_API_KEY = os.environ.get("ZEP_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_v4_user_thread_graph_search_and_cleanup() -> None:
    if not ZEP_API_KEY:
        pytest.skip("ZEP_API_KEY is required for this live test")

    zep = AsyncZep(api_key=ZEP_API_KEY)
    user = None
    thread = None
    graph = None
    try:
        suffix = uuid4().hex
        user = await create_user(
            zep,
            first_name="ADK",
            last_name="Integration",
            email=f"adk-{suffix}@example.com",
        )
        thread = await create_thread(zep, user_uuid=user.uuid_)
        graph = await zep.graph.create(name=f"adk-live-{suffix}")

        user_graph = await zep.graph.get(user.uuid_)
        assert user_graph.uuid_

        user_context = MagicMock()
        user_context.state = {"zep_graph_uuid": user.uuid_}
        user_search_result = await ZepGraphSearchTool(
            zep_client=zep,
            scope="edges",
        ).run_async(
            args={"query": "ADK integration"},
            tool_context=user_context,
        )
        assert "Graph search failed" not in user_search_result

        context = MagicMock()
        context.state = {"zep_graph_uuid": graph.uuid_}
        search_tool = ZepGraphSearchTool(zep_client=zep, scope="edges")
        search_result = await search_tool.run_async(
            args={"query": "ADK integration"},
            tool_context=context,
        )
        assert isinstance(search_result, str)
        assert "Graph search failed" not in search_result

        memory_result = await ZepMemoryService(zep=zep).search_memory(
            app_name="adk-live-test",
            user_id=user.uuid_,
            query="ADK integration",
        )
        assert isinstance(memory_result.memories, list)
    finally:
        if graph is not None:
            await zep.graph.delete(graph.uuid_)
        if thread is not None:
            await zep.thread.delete(thread.uuid_)
        if user is not None:
            await zep.user.delete(user.uuid_)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_adk_turn_persists_with_v4_user_and_thread() -> None:
    if not ZEP_API_KEY:
        pytest.skip("ZEP_API_KEY is required for this live test")
    if not GOOGLE_API_KEY:
        pytest.skip("GOOGLE_API_KEY is required for the Gemini live test")

    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
    zep = AsyncZep(api_key=ZEP_API_KEY)
    user = None
    thread = None
    try:
        suffix = uuid4().hex
        user = await create_user(
            zep,
            first_name="ADK",
            last_name="Integration",
            email=f"adk-agent-{suffix}@example.com",
        )
        thread = await create_thread(zep, user_uuid=user.uuid_)
        app_name = f"adk-live-{suffix}"
        app_user_id = f"app-user-{suffix}"
        session_id = f"app-session-{suffix}"
        session_service = InMemorySessionService()
        await session_service.create_session(
            app_name=app_name,
            user_id=app_user_id,
            session_id=session_id,
            state={
                "zep_user_uuid": user.uuid_,
                "zep_thread_uuid": thread.uuid_,
                "zep_graph_uuid": user.uuid_,
            },
        )

        async def context_builder(_context: object) -> str:
            return "Trusted test context."

        agent = Agent(
            name="zep_live_agent",
            model="gemini-2.5-flash",
            instruction="Answer the user's greeting.",
            tools=[ZepContextTool(zep_client=zep, context_builder=context_builder)],
            after_model_callback=create_after_model_callback(zep_client=zep),
        )
        runner = Runner(
            agent=agent,
            app_name=app_name,
            session_service=session_service,
        )
        responses: list[str] = []
        async for event in runner.run_async(
            user_id=app_user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text="Hello.")],
            ),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                responses.extend(
                    part.text for part in event.content.parts if hasattr(part, "text") and part.text
                )

        assert responses
    finally:
        if thread is not None:
            await zep.thread.delete(thread.uuid_)
        if user is not None:
            await zep.user.delete(user.uuid_)
