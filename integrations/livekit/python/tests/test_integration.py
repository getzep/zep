from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from livekit.agents.llm.chat_context import ChatContext
from zep_cloud.client import AsyncZep

from zep_livekit import ZepUserAgent, create_thread, create_user

pytestmark = pytest.mark.integration

ZEP_API_KEY = os.environ.get("ZEP_API_KEY")
if not ZEP_API_KEY:
    pytest.skip("ZEP_API_KEY is required for the live test", allow_module_level=True)


class _UserMessage:
    def __init__(self, text: str) -> None:
        self.text_content = text


class _AssistantItem:
    name = None


async def _turn(agent: ZepUserAgent, text: str) -> ChatContext:
    context = ChatContext.empty()
    await agent.on_user_turn_completed(context, _UserMessage(text))
    return context


def _context_text(context: ChatContext) -> str:
    return " ".join(
        "".join(item.content) if isinstance(item.content, list) else str(item.content)
        for item in context.items
        if getattr(item, "role", None) == "system"
    )


@pytest.mark.asyncio
async def test_v4_user_thread_persistence_and_cross_thread_recall() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    suffix = uuid4().hex[:10]
    user = None
    threads = []
    try:
        user = await create_user(
            zep,
            first_name="LiveKit",
            last_name="Integration",
            email=f"livekit-{suffix}@example.com",
        )
        first_thread = await create_thread(zep, user_uuid=user.uuid_)
        threads.append(first_thread)
        first_agent = ZepUserAgent(
            zep_client=zep,
            user_uuid=user.uuid_,
            thread_uuid=first_thread.uuid_,
            instructions="You are a helpful assistant.",
        )

        seeds = [
            (
                "My company is Acme Orchard and I work as an apple grower.",
                "I will remember that you grow apples at Acme Orchard.",
            ),
            (
                "I live in Portland and enjoy hiking in Forest Park.",
                "I will remember Portland and Forest Park.",
            ),
        ]
        for user_text, assistant_text in seeds:
            await _turn(first_agent, user_text)
            await first_agent._store_assistant_message(assistant_text, _AssistantItem())

        created_user = await zep.user.get(user.uuid_)
        assert created_user.first_name == "LiveKit"
        assert created_user.email == f"livekit-{suffix}@example.com"

        message_page = await zep.thread.list_messages(first_thread.uuid_, page_size=20)
        messages = message_page.items or []
        assert sum(message.role == "user" for message in messages) >= 2
        assert sum(message.role == "assistant" for message in messages) >= 2

        keywords = ("acme orchard", "apple grower", "portland", "forest park")
        search_text = ""
        deadline = asyncio.get_running_loop().time() + 180
        while asyncio.get_running_loop().time() < deadline:
            results = await zep.graph.search_edges(
                user.uuid_,
                query="company work location hobbies",
                page_size=20,
            )
            search_text = " ".join(edge.fact for edge in (results.items or [])).lower()
            if any(word in search_text for word in keywords):
                break
            await asyncio.sleep(3)
        assert any(word in search_text for word in keywords), (
            "The user graph did not return the seeded facts."
        )

        second_thread = await create_thread(zep, user_uuid=user.uuid_)
        threads.append(second_thread)
        second_agent = ZepUserAgent(
            zep_client=zep,
            user_uuid=user.uuid_,
            thread_uuid=second_thread.uuid_,
            instructions="You are a helpful assistant.",
        )
        context = await _turn(second_agent, "What do you know about my work and hobbies?")
        assert any(word in _context_text(context).lower() for word in keywords)
    finally:
        for thread in threads:
            await zep.thread.delete(thread.uuid_)
        if user is not None:
            await zep.user.delete(user.uuid_)
