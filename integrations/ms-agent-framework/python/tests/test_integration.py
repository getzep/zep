"""Live V4 lifecycle tests for the Microsoft Agent Framework integration."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

if not ZEP_API_KEY:
    pytest.skip("ZEP_API_KEY required for integration tests", allow_module_level=True)

from agent_framework import Agent, Message  # noqa: E402
from agent_framework.openai import OpenAIChatClient  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_ms_agent_framework import (  # noqa: E402
    ZepContextProvider,
    create_thread,
    create_user,
)


def _identity_suffix() -> str:
    return uuid4().hex[:8]


def _context(messages: list[Message], response: list[Message] | None = None) -> MagicMock:
    value = MagicMock()
    value.input_messages = messages
    value.extend_instructions = MagicMock()
    if response is None:
        value.response = None
    else:
        value.response = SimpleNamespace(messages=response)
    return value


async def _persist_turn(
    provider: ZepContextProvider,
    user_text: str,
    assistant_text: str,
) -> None:
    await provider.before_run(
        agent=MagicMock(),
        session=MagicMock(),
        context=_context([Message("user", [user_text])]),
        state={},
    )
    await provider.after_run(
        agent=MagicMock(),
        session=MagicMock(),
        context=_context([], [Message("assistant", [assistant_text])]),
        state={},
    )


async def _wait_for_search_result(
    zep: AsyncZep,
    graph_uuid: str,
    *,
    query: str,
    timeout_seconds: int = 120,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        page = await zep.graph.search_edges(graph_uuid, query=query, page_size=5)
        if page.items:
            return True
        await asyncio.sleep(3)
    return False


async def _delete_resources(
    zep: AsyncZep,
    thread_uuids: list[str],
    user_uuid: str | None,
) -> None:
    errors: list[Exception] = []
    for thread_uuid in thread_uuids:
        try:
            await zep.thread.delete(thread_uuid)
        except Exception as exc:
            errors.append(exc)
    if user_uuid is not None:
        try:
            await zep.user.delete(user_uuid)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError(f"Failed to delete {len(errors)} live test resource(s)") from errors[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_helpers_and_before_after_lifecycle() -> None:
    """Create UUID resources, persist both roles, and run the setup hook."""
    zep = AsyncZep(api_key=ZEP_API_KEY)
    suffix = _identity_suffix()
    user_uuid: str | None = None
    thread_uuids: list[str] = []
    hook_calls: list[str] = []

    async def on_created(_client: AsyncZep, created_user_uuid: str) -> None:
        hook_calls.append(created_user_uuid)

    try:
        user = await create_user(
            zep,
            first_name="Lifecycle",
            last_name="Test",
            email=f"lifecycle-{suffix}@example.com",
            on_created=on_created,
        )
        user_uuid = cast(str, user.uuid_)
        assert user_uuid
        thread = await create_thread(zep, user_uuid=user_uuid)
        thread_uuid = cast(str, thread.uuid_)
        assert thread_uuid
        thread_uuids.append(thread_uuid)

        provider = ZepContextProvider(
            zep_client=zep,
            user_uuid=user_uuid,
            thread_uuid=thread_uuid,
            user_message_name="Lifecycle Test",
        )
        await _persist_turn(
            provider,
            "I work at Acme as a data scientist.",
            "I will remember that you work at Acme.",
        )

        page = await zep.thread.list_messages(thread_uuid, page_size=20)
        user_messages = [message for message in page.items if message.role == "user"]
        assistant_messages = [message for message in page.items if message.role == "assistant"]

        assert user.first_name == "Lifecycle"
        assert user.last_name == "Test"
        assert user.email == f"lifecycle-{suffix}@example.com"
        assert hook_calls == [user_uuid]
        assert any("Acme" in (message.content or "") for message in user_messages)
        assert any("remember" in (message.content or "") for message in assistant_messages)
    finally:
        await _delete_resources(zep, thread_uuids, user_uuid)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_lifecycle_recalls_across_threads() -> None:
    """Persist model turns and recall a user fact from a second thread."""
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY required for the model-driven integration test")

    zep = AsyncZep(api_key=ZEP_API_KEY)
    suffix = _identity_suffix()
    user_uuid: str | None = None
    thread_uuids: list[str] = []
    hook_calls: list[str] = []

    async def on_created(_client: AsyncZep, created_user_uuid: str) -> None:
        hook_calls.append(created_user_uuid)

    def build_agent(thread_uuid: str) -> Agent:
        return Agent(
            OpenAIChatClient(model=OPENAI_MODEL, api_key=OPENAI_API_KEY),
            instructions=(
                "You are a helpful assistant with long-term memory. Use Zep context "
                "when it contains facts about the user. Be concise."
            ),
            context_providers=[
                ZepContextProvider(
                    zep_client=zep,
                    user_uuid=cast(str, user_uuid),
                    thread_uuid=thread_uuid,
                    graph_uuid=cast(str, user_uuid),
                    user_message_name="Recall Test",
                )
            ],
        )

    try:
        user = await create_user(
            zep,
            first_name="Recall",
            last_name="Test",
            email=f"recall-{suffix}@example.com",
            on_created=on_created,
        )
        user_uuid = cast(str, user.uuid_)
        assert user_uuid
        first_thread = await create_thread(zep, user_uuid=user_uuid)
        first_thread_uuid = cast(str, first_thread.uuid_)
        assert first_thread_uuid
        thread_uuids.append(first_thread_uuid)

        agent = build_agent(first_thread_uuid)
        seed_prompts = [
            "I work at Acme as a data scientist.",
            "I live in Portland and enjoy landscape photography.",
        ]
        for prompt in seed_prompts:
            result = await agent.run(prompt)
            assert result.text.strip()

        assert hook_calls == [user_uuid]
        assert await _wait_for_search_result(
            zep,
            user_uuid,
            query="Acme data scientist Portland",
        )

        second_thread = await create_thread(zep, user_uuid=user_uuid)
        second_thread_uuid = cast(str, second_thread.uuid_)
        assert second_thread_uuid
        thread_uuids.append(second_thread_uuid)

        result = await build_agent(second_thread_uuid).run("Where do I work, and what do I do?")
        assert result.text.strip()
        assert "acme" in result.text.lower() or "data scientist" in result.text.lower()
        assert hook_calls == [user_uuid]
    finally:
        await _delete_resources(zep, thread_uuids, user_uuid)
