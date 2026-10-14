"""Live lifecycle test for the Zep AutoGen integration.

Run with:
    uv run pytest tests/test_integration.py -v -s -m integration

Requires ZEP_API_KEY and OPENAI_API_KEY.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import pytest

if not os.environ.get("ZEP_API_KEY") or not os.environ.get("OPENAI_API_KEY"):
    pytest.skip(
        "ZEP_API_KEY and OPENAI_API_KEY required for integration tests",
        allow_module_level=True,
    )

from autogen_agentchat.agents import AssistantAgent  # noqa: E402
from autogen_core.memory import MemoryContent, MemoryMimeType  # noqa: E402
from autogen_ext.models.openai import OpenAIChatCompletionClient  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_autogen import ZepUserMemory, create_thread, create_user  # noqa: E402

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _wait_for_episodes(zep: AsyncZep, graph_uuid: str, timeout: int = 120) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            page = await zep.graph.episode.list(graph_uuid, page_size=20)
        except Exception as exc:
            logger.warning("Episode polling failed: %s", exc)
            await asyncio.sleep(3)
            continue
        episodes = page.items
        if episodes and all(episode.processed for episode in episodes):
            return
        await asyncio.sleep(3)
    logger.warning("Timed out waiting for episode processing.")


def _build_agent(
    zep: AsyncZep, user_uuid: str, thread_uuid: str
) -> tuple[AssistantAgent, ZepUserMemory]:
    memory = ZepUserMemory(
        client=zep,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
    )
    agent = AssistantAgent(
        name="MemoryAwareAssistant",
        model_client=OpenAIChatCompletionClient(model=OPENAI_MODEL),
        memory=[memory],
        system_message=(
            "You are a helpful assistant with long-term memory. Use any injected "
            "context to answer questions about the user. Be concise."
        ),
    )
    return agent, memory


async def _store_turn(
    memory: ZepUserMemory, content: str, role: str, name: str | None = None
) -> None:
    await memory.add(
        MemoryContent(
            content=content,
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": role, "name": name},
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    """Verify metadata, both message roles, processing, and cross-thread recall."""
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    suffix = str(time.time_ns())
    first_name = "AutoGenLiveTest"
    email = f"autogen-live-{suffix}@example.com"
    user_uuid: str | None = None
    thread_uuids: list[str] = []

    try:
        user = await create_user(
            zep,
            first_name=first_name,
            last_name="User",
            email=email,
        )
        user_uuid = user.uuid_
        first_thread = await create_thread(zep, user_uuid=user_uuid)
        thread_uuids.append(first_thread.uuid_)

        agent, memory = _build_agent(zep, user_uuid, first_thread.uuid_)
        for message in (
            "My name is AutoGenLiveTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ):
            await _store_turn(memory, message, "user", first_name)
            response = await agent.run(task=message)
            reply = str(response.messages[-1].content)
            assert reply
            await _store_turn(memory, reply, "assistant")

        saved_user = await zep.user.get(user_uuid)
        assert saved_user.first_name == first_name
        assert saved_user.last_name == "User"
        assert saved_user.email == email

        messages_page = await zep.thread.list_messages(first_thread.uuid_, page_size=20)
        messages = messages_page.items
        assert sum(message.role == "user" for message in messages) >= 2
        assert sum(message.role == "assistant" for message in messages) >= 2

        await _wait_for_episodes(zep, user_uuid)

        second_thread = await create_thread(zep, user_uuid=user_uuid)
        thread_uuids.append(second_thread.uuid_)
        second_agent, second_memory = _build_agent(zep, user_uuid, second_thread.uuid_)
        question = "What do you know about me?"
        await _store_turn(second_memory, question, "user", first_name)
        response = await second_agent.run(task=question)
        recall = str(response.messages[-1].content).lower()
        assert any(
            keyword in recall
            for keyword in ("acme", "data scientist", "portland", "hiking", "photography")
        ), f"No cross-thread recall in: {recall}"
    finally:
        for thread_uuid in thread_uuids:
            try:
                await zep.thread.delete(thread_uuid)
            except Exception as exc:
                logger.warning("Could not delete test thread %s: %s", thread_uuid, exc)
        if user_uuid:
            try:
                await zep.user.delete(user_uuid)
            except Exception as exc:
                logger.warning("Could not delete test user %s: %s", user_uuid, exc)
