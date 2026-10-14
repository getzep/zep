"""Live CrewAI and Zep V4 integration test."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("ZEP_API_KEY") or not os.getenv("OPENAI_API_KEY"),
    reason="ZEP_API_KEY and OPENAI_API_KEY are required",
)
def test_integration_full_lifecycle() -> None:
    from crewai import Agent, Crew, Process, Task
    from zep_cloud.client import Zep

    from zep_crewai import ZepUserStorage, create_search_tool, ensure_thread, ensure_user

    zep = Zep(api_key=os.environ["ZEP_API_KEY"])
    suffix = uuid4().hex[:8]
    user_uuid = None
    thread_uuids: list[str] = []
    try:
        user, created = ensure_user(
            zep,
            first_name="CrewAI V4",
            last_name="Integration",
            email=f"crewai-{suffix}@example.com",
        )
        assert created is True
        user_uuid = user.uuid_
        thread, _ = ensure_thread(zep, user_uuid=user_uuid)
        thread_uuids.append(thread.uuid_)
        recall_thread, _ = ensure_thread(zep, user_uuid=user_uuid)
        thread_uuids.append(recall_thread.uuid_)
        first = ZepUserStorage(zep, user_uuid=user_uuid, thread_uuid=thread.uuid_)
        recall = ZepUserStorage(zep, user_uuid=user_uuid, thread_uuid=recall_thread.uuid_)

        created_user = zep.user.get(user_uuid)
        assert created_user.first_name == "CrewAI V4"
        assert created_user.last_name == "Integration"
        assert created_user.email == f"crewai-{suffix}@example.com"

        first.save(
            "My name is Casey. I work at Acme as a data scientist in Portland.",
            metadata={"type": "message", "role": "user", "name": "Casey"},
        )
        first.save(
            "I will remember that Casey works at Acme in Portland.",
            metadata={"type": "message", "role": "assistant", "name": "Assistant"},
        )

        messages = zep.thread.list_messages(thread.uuid_, page_size=20).items or []
        assert any(message.role == "user" for message in messages)
        assert any(message.role == "assistant" for message in messages)
        assert first.get_context() is not None

        deadline = time.monotonic() + 300
        facts = []
        while time.monotonic() < deadline:
            page = zep.graph.search_edges(
                user.uuid_,
                query="Where does Casey work and live?",
                page_size=10,
            )
            facts = page.items or []
            if facts:
                break
            time.sleep(5)
        assert facts, "Zep did not process the saved memory before the timeout"

        results = recall.search("Where does Casey work and live?")
        assert results
        assert any(word in str(results).lower() for word in ("acme", "portland"))

        agent = Agent(
            role="Personal assistant",
            goal="Answer questions from the user's Zep memory.",
            backstory="Search Zep memory before answering.",
            tools=[create_search_tool(zep, graph_uuid=user.uuid_)],
            llm=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            verbose=False,
        )
        task = Task(
            description="Search memory and state Casey's employer and city.",
            expected_output="A sentence with the employer and city.",
            agent=agent,
        )
        output = Crew(
            agents=[agent], tasks=[task], process=Process.sequential, verbose=False
        ).kickoff()
        assert "acme" in str(output).lower() or "portland" in str(output).lower()
    finally:
        try:
            for thread_uuid in thread_uuids:
                zep.thread.delete(thread_uuid)
        finally:
            if user_uuid is not None:
                zep.user.delete(user_uuid)
