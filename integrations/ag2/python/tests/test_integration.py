"""Live lifecycle test for the AG2 V4 integration."""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, create_thread, create_user, register_all_tools

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

if not ZEP_API_KEY or not OPENAI_API_KEY:
    pytest.skip("ZEP_API_KEY and OPENAI_API_KEY are required", allow_module_level=True)


async def _wait_for_episodes(zep: AsyncZep, graph_uuid: str, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page = await zep.graph.episode.list(graph_uuid, page_size=20)
        if page.items and all(episode.processed for episode in page.items):
            return
        await asyncio.sleep(3)
    raise TimeoutError(f"Zep episodes were not processed within {timeout} seconds.")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    user_uuid: str | None = None
    thread_uuids: list[str] = []
    try:
        user = await create_user(
            zep,
            first_name="IntegTest",
            last_name="User",
            email=f"ag2-integ-{os.urandom(6).hex()}@example.com",
        )
        user_uuid = user.uuid_
        assert user_uuid is not None
        graph_uuid = user.uuid_
        first_thread = await create_thread(zep, user_uuid=user_uuid)
        assert first_thread.uuid_ is not None
        thread_uuids.append(first_thread.uuid_)

        manager = ZepMemoryManager(
            zep,
            user_uuid=user_uuid,
            thread_uuid=first_thread.uuid_,
            graph_uuid=graph_uuid,
        )
        await manager.add_messages(
            [
                {
                    "role": "user",
                    "content": "My name is IntegTest. I work at Acme Corp as a data scientist.",
                },
                {"role": "assistant", "content": "Noted: a data scientist at Acme Corp."},
                {
                    "role": "user",
                    "content": "I live in Portland and I love hiking and photography.",
                },
                {"role": "assistant", "content": "Noted: Portland, hiking, and photography."},
            ]
        )

        stored_user = await zep.user.get(user_uuid)
        assert stored_user.first_name == "IntegTest"
        assert stored_user.last_name == "User"
        assert stored_user.email == user.email
        messages = await zep.thread.list_messages(first_thread.uuid_, page_size=20)
        assert any(message.role == "user" for message in messages.items)
        assert any(message.role == "assistant" for message in messages.items)

        await _wait_for_episodes(zep, graph_uuid)

        second_thread = await create_thread(zep, user_uuid=user_uuid)
        assert second_thread.uuid_ is not None
        thread_uuids.append(second_thread.uuid_)
        second_manager = ZepMemoryManager(
            zep,
            user_uuid=user_uuid,
            thread_uuid=second_thread.uuid_,
            graph_uuid=graph_uuid,
        )
        context = await second_manager.get_memory_context(
            query="What do we know about IntegTest's job, location, and hobbies?"
        )
        expected = ("acme", "data scientist", "portland", "hiking", "photography")
        assert any(term in context.lower() for term in expected), f"No recalled facts: {context}"

        llm_config = LLMConfig({"model": OPENAI_MODEL, "api_key": OPENAI_API_KEY})
        assistant = AssistantAgent(
            name="assistant",
            llm_config=llm_config,
            system_message="Use the supplied Zep context to answer questions about the user.",
        )
        user_proxy = UserProxyAgent(
            name="user",
            human_input_mode="NEVER",
            code_execution_config=False,
            is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
        )
        register_all_tools(assistant, user_proxy, zep, graph_uuid, thread_uuid=second_thread.uuid_)
        await second_manager.enrich_system_message(assistant, query="IntegTest profile and hobbies")
        result = user_proxy.initiate_chat(
            assistant,
            message=(
                "Where do I work, what is my role, and where do I live? "
                "Answer in one sentence, then say TERMINATE."
            ),
            max_turns=2,
        )
        transcript = " ".join(
            str(message.get("content") or "") for message in (result.chat_history or [])
        ).lower()
        assert any(term in transcript for term in expected), f"No recalled facts: {transcript}"
    finally:
        for thread_uuid in thread_uuids:
            try:
                await zep.thread.delete(thread_uuid)
            except Exception:
                pass
        if user_uuid:
            await zep.user.delete(user_uuid)
