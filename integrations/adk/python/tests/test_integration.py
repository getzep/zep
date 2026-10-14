"""Live tests for the Google ADK integration and Zep v4 SDK."""

from __future__ import annotations

import asyncio
import os
import time
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

        created_user = await zep.user.get(user.uuid_)
        assert created_user.first_name == "ADK"
        assert created_user.last_name == "Integration"
        assert created_user.email == f"adk-{suffix}@example.com"
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

        auto_search_result = await ZepGraphSearchTool(
            zep_client=zep,
            scope="auto",
        ).run_async(
            args={"query": "ADK integration"},
            tool_context=user_context,
        )
        assert isinstance(auto_search_result, str)
        assert "Graph search failed" not in auto_search_result

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_thread_recall_uses_the_user_graph() -> None:
    if not ZEP_API_KEY:
        pytest.skip("ZEP_API_KEY is required for this live test")
    if not GOOGLE_API_KEY:
        pytest.skip("GOOGLE_API_KEY is required for the Gemini live test")

    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    zep = AsyncZep(api_key=ZEP_API_KEY)
    user = None
    threads = []
    try:
        suffix = uuid4().hex
        user = await create_user(zep, first_name="ADK", last_name="Recall")
        seed_thread = await create_thread(zep, user_uuid=user.uuid_)
        recall_thread = await create_thread(zep, user_uuid=user.uuid_)
        search_thread = await create_thread(zep, user_uuid=user.uuid_)
        threads.extend([seed_thread, recall_thread, search_thread])

        app_name = f"adk-recall-{suffix}"
        app_user_id = f"app-user-{suffix}"
        seed_session = f"seed-{suffix}"
        recall_session = f"recall-{suffix}"
        search_session = f"search-{suffix}"
        sessions = InMemorySessionService()
        for session_id, thread in (
            (seed_session, seed_thread),
            (recall_session, recall_thread),
            (search_session, search_thread),
        ):
            await sessions.create_session(
                app_name=app_name,
                user_id=app_user_id,
                session_id=session_id,
                state={
                    "zep_user_uuid": user.uuid_,
                    "zep_thread_uuid": thread.uuid_,
                    "zep_graph_uuid": user.uuid_,
                },
            )

        agent = Agent(
            name="zep_cross_thread_agent",
            model="gemini-2.5-flash",
            instruction="Use Zep context when it contains a fact about the user.",
            tools=[ZepContextTool(zep_client=zep)],
            after_model_callback=create_after_model_callback(zep_client=zep),
        )
        runner = Runner(agent=agent, app_name=app_name, session_service=sessions)

        async def send(session_id: str, text: str, active_runner=runner) -> str:
            parts = []
            async for event in active_runner.run_async(
                user_id=app_user_id,
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=text)]),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    parts.extend(
                        part.text for part in event.content.parts if getattr(part, "text", None)
                    )
            return " ".join(parts)

        seed = (
            "Remember this fact about me: my favorite hiking place is Hood River "
            "in Oregon, and I enjoy landscape photography."
        )
        assert await send(seed_session, seed)

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = await zep.graph.search_edges(
                user.uuid_,
                query="favorite hiking place",
                page_size=10,
            )
            facts = [edge.fact or "" for edge in (result.items or [])]
            if any("hood river" in fact.lower() for fact in facts):
                break
            await asyncio.sleep(3)
        else:
            pytest.fail("Zep did not index the seeded fact before the timeout")

        recalled = await send(recall_session, "Where do I like to go hiking?")
        assert "hood river" in recalled.lower()

        search_agent = Agent(
            name="zep_graph_search_agent",
            model="gemini-2.5-flash",
            instruction=(
                "You must call search_user_memory to answer questions about the user's "
                "past conversations. Do not answer from prior knowledge."
            ),
            tools=[
                ZepGraphSearchTool(
                    zep_client=zep,
                    name="search_user_memory",
                    scope="edges",
                )
            ],
        )
        search_runner = Runner(
            agent=search_agent,
            app_name=app_name,
            session_service=sessions,
        )
        searched = await send(
            search_session,
            "Use search_user_memory to find my favorite hiking place. What does it return?",
            search_runner,
        )
        assert "hood river" in searched.lower()
    finally:
        for thread in threads:
            await zep.thread.delete(thread.uuid_)
        if user is not None:
            await zep.user.delete(user.uuid_)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tool_call_persistence_excludes_function_messages() -> None:
    if not ZEP_API_KEY:
        pytest.skip("ZEP_API_KEY is required for this live test")
    if not GOOGLE_API_KEY:
        pytest.skip("GOOGLE_API_KEY is required for the Gemini live test")

    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    def get_current_weather(city: str) -> dict[str, str]:
        """Return test weather data."""
        return {"city": city, "temperature": "72°F", "condition": "sunny"}

    zep = AsyncZep(api_key=ZEP_API_KEY)
    user = None
    thread = None
    try:
        suffix = uuid4().hex
        user = await create_user(zep, first_name="ADK", last_name="Tool")
        thread = await create_thread(zep, user_uuid=user.uuid_)
        app_name = f"adk-tool-{suffix}"
        app_user_id = f"app-user-{suffix}"
        session_id = f"tool-{suffix}"
        sessions = InMemorySessionService()
        await sessions.create_session(
            app_name=app_name,
            user_id=app_user_id,
            session_id=session_id,
            state={
                "zep_user_uuid": user.uuid_,
                "zep_thread_uuid": thread.uuid_,
                "zep_graph_uuid": user.uuid_,
            },
        )
        agent = Agent(
            name="zep_tool_persistence_agent",
            model="gemini-2.5-flash",
            instruction="Use the weather tool when the user asks for weather.",
            tools=[ZepContextTool(zep_client=zep), get_current_weather],
            after_model_callback=create_after_model_callback(zep_client=zep),
        )
        runner = Runner(agent=agent, app_name=app_name, session_service=sessions)
        responses = []
        async for event in runner.run_async(
            user_id=app_user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(text="Use the weather tool for Portland and tell me the result.")
                ],
            ),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                responses.extend(
                    part.text for part in event.content.parts if getattr(part, "text", None)
                )
        assert responses

        message_page = await zep.thread.list_messages(thread.uuid_, page_size=20)
        messages = message_page.items or []
        user_messages = [message for message in messages if message.role == "user"]
        assistant_messages = [message for message in messages if message.role == "assistant"]
        tool_messages = [message for message in messages if message.role in ("tool", "function")]
        assert len(user_messages) == 1
        assert len(assistant_messages) == 1
        assert not tool_messages
        assert any(
            token in (assistant_messages[0].content or "").lower()
            for token in ("72", "sunny", "portland")
        )
    finally:
        if thread is not None:
            await zep.thread.delete(thread.uuid_)
        if user is not None:
            await zep.user.delete(user.uuid_)
