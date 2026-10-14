"""Tests for the ADK memory service and Zep v4 graph search."""

from unittest.mock import AsyncMock, MagicMock

import pytest

USER_UUID = "7a8fbf8e-5153-4c1b-bb4c-5d7f0f6c1f21"
GRAPH_UUID = "34eb54ba-b3d5-4b7a-9d54-f4458f0ee343"


def _page(items: list[MagicMock] | None = None) -> MagicMock:
    page = MagicMock()
    page.items = items or []
    return page


def _item(**values: str | None) -> MagicMock:
    item = MagicMock()
    for name, value in values.items():
        setattr(item, name, value)
    return item


def _make_client() -> MagicMock:
    client = MagicMock()
    client.user.get = AsyncMock(return_value=MagicMock(uuid_=GRAPH_UUID))
    client.graph.get_context = AsyncMock(return_value=MagicMock(context="Saved context"))
    for scope in ("edges", "nodes", "episodes", "observations", "thread_summaries"):
        setattr(client.graph, f"search_{scope}", AsyncMock(return_value=_page()))
    return client


@pytest.mark.asyncio
async def test_add_session_to_memory_does_not_call_zep() -> None:
    from zep_adk.memory_service import ZepMemoryService

    client = _make_client()
    await ZepMemoryService(zep=client).add_session_to_memory(MagicMock())

    client.user.get.assert_not_awaited()
    client.graph.get_context.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "item", "expected"),
    [
        ("edges", _item(fact="Jane works at Acme."), "Jane works at Acme."),
        ("nodes", _item(name="Acme", summary="A company"), "Acme: A company"),
        ("episodes", _item(content="Original message"), "Original message"),
        (
            "observations",
            _item(name="Preference", summary="Prefers tea"),
            "Preference: Prefers tea",
        ),
        (
            "thread_summaries",
            _item(name=None, summary="Build a tool"),
            "Build a tool",
        ),
    ],
)
async def test_search_memory_maps_v4_scope_items(
    scope: str, item: MagicMock, expected: str
) -> None:
    from zep_adk.memory_service import ZepMemoryService

    client = _make_client()
    getattr(client.graph, f"search_{scope}").return_value = _page([item])

    response = await ZepMemoryService(zep=client, scope=scope).search_memory(
        app_name="app",
        user_id=USER_UUID,
        query="work",
    )

    assert [memory.content.parts[0].text for memory in response.memories] == [expected]
    client.user.get.assert_awaited_once_with(USER_UUID)
    getattr(client.graph, f"search_{scope}").assert_awaited_once_with(GRAPH_UUID, query="work")


@pytest.mark.asyncio
async def test_search_memory_uses_page_size_and_user_graph_uuid() -> None:
    from zep_adk.memory_service import ZepMemoryService

    client = _make_client()
    service = ZepMemoryService(zep=client, scope="edges", limit=4)

    await service.search_memory(app_name="app", user_id=USER_UUID, query="work")

    client.graph.search_edges.assert_awaited_once_with(GRAPH_UUID, query="work", page_size=4)


@pytest.mark.asyncio
async def test_search_memory_auto_returns_context_as_one_entry() -> None:
    from zep_adk.memory_service import ZepMemoryService

    client = _make_client()
    response = await ZepMemoryService(zep=client, scope="auto").search_memory(
        app_name="app",
        user_id=USER_UUID,
        query="work",
    )

    assert [memory.content.parts[0].text for memory in response.memories] == ["Saved context"]
    client.graph.get_context.assert_awaited_once_with(
        GRAPH_UUID,
        query="work",
        include_results=True,
    )


@pytest.mark.asyncio
async def test_search_memory_skips_lookup_for_non_uuid_adk_user_id() -> None:
    from zep_adk.memory_service import ZepMemoryService

    client = _make_client()
    response = await ZepMemoryService(zep=client).search_memory(
        app_name="app",
        user_id="application-user-id",
        query="work",
    )

    assert response.memories == []
    client.user.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_memory_returns_empty_response_when_zep_fails() -> None:
    from zep_adk.memory_service import ZepMemoryService

    client = _make_client()
    client.graph.search_edges.side_effect = RuntimeError("Zep is down")

    response = await ZepMemoryService(zep=client).search_memory(
        app_name="app",
        user_id=USER_UUID,
        query="work",
    )

    assert response.memories == []


def test_memory_service_rejects_unsupported_scope() -> None:
    from zep_adk.memory_service import ZepMemoryService

    with pytest.raises(ValueError, match="Unsupported Zep search scope"):
        ZepMemoryService(zep=_make_client(), scope="unknown")
