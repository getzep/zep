"""Live integration tests against Zep Cloud (skipped without ZEP_API_KEY)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")


@pytest.fixture
def zep_client():  # type: ignore[no-untyped-def]
    if not ZEP_API_KEY:
        pytest.skip("ZEP_API_KEY not set")
    from zep_cloud.client import AsyncZep

    return AsyncZep(api_key=ZEP_API_KEY)


@pytest.mark.asyncio
async def test_store_round_trip(zep_client) -> None:  # type: ignore[no-untyped-def]
    from zep_strands import ZepMemoryStore, ensure_thread, ensure_user

    suffix = uuid4().hex[:8]
    user_id = f"strands-it-user-{suffix}"
    thread_id = f"strands-it-thread-{suffix}"

    await ensure_user(
        zep_client,
        user_id=user_id,
        first_name="Integration",
        last_name="Tester",
        email=f"strands-{suffix}@example.com",
    )
    await ensure_thread(zep_client, thread_id=thread_id, user_id=user_id)

    store = ZepMemoryStore(
        zep_client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
        first_name="Integration",
        last_name="Tester",
        writable=True,
        extraction=True,
        search_scope="edges",
    )

    await store.add_messages(
        [
            {
                "role": "user",
                "content": [{"text": "I am a ceramicist based in Santa Fe."}],
            },
            {
                "role": "assistant",
                "content": [{"text": "I'll remember that."}],
            },
        ]
    )

    # Also exercise the single-content write path.
    await store.add("Prefers matte glazes over glossy finishes.")

    # Search may be empty immediately (async ingestion); just assert no raise.
    entries = await store.search("ceramics")
    assert isinstance(entries, list)
