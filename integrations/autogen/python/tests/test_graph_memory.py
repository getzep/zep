from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import UserMessage
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.types import NodeSearchFilters

from zep_autogen import ZepGraphMemory


def _make_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock()
    client.graph.episode.list = AsyncMock(
        return_value=SimpleNamespace(
            items=[
                SimpleNamespace(content="Latest episode"),
                SimpleNamespace(content="Earlier episode"),
            ]
        )
    )
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="Graph context"))
    client.graph.search_edges = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.search_nodes = AsyncMock(
        return_value=SimpleNamespace(items=[SimpleNamespace(name="Python", summary="A language")])
    )
    client.graph.search_episodes = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.search_observations = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.search_thread_summaries = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.delete = AsyncMock()
    return client


async def _context_with_message() -> UnboundedChatCompletionContext:
    context = UnboundedChatCompletionContext()
    await context.add_message(UserMessage(content="hello", source="user"))
    return context


def test_graph_memory_requires_graph_uuid() -> None:
    with pytest.raises(ValueError, match="graph_uuid is required"):
        ZepGraphMemory(client=_make_client(), graph_uuid="")


@pytest.mark.asyncio
async def test_add_writes_episode_to_shared_graph() -> None:
    client = _make_client()
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")
    content = MemoryContent(
        content="A shared fact",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "data"},
    )

    await memory.add(content)

    client.graph.episode.add.assert_awaited_once_with(
        "graph-1",
        type="text",
        data="A shared fact",
    )


@pytest.mark.asyncio
async def test_add_maps_json_and_message_types() -> None:
    client = _make_client()
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")
    json_content = MemoryContent(
        content='{"name":"Zep"}',
        mime_type=MemoryMimeType.JSON,
        metadata={"type": "data"},
    )
    message_content = MemoryContent(
        content="Hello",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message"},
    )

    await memory.add(json_content)
    await memory.add(message_content)

    assert client.graph.episode.add.call_args_list[0].kwargs["type"] == "json"
    assert client.graph.episode.add.call_args_list[1].kwargs["type"] == "message"


@pytest.mark.asyncio
async def test_add_swallows_sdk_error(caplog) -> None:
    client = _make_client()
    client.graph.episode.add.side_effect = RuntimeError("unavailable")
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")
    content = MemoryContent(content="fact", mime_type=MemoryMimeType.TEXT)

    await memory.add(content)

    assert "Failed to add Zep graph episode" in caplog.text


@pytest.mark.asyncio
async def test_query_scope_searches_nodes_with_typed_filters() -> None:
    client = _make_client()
    memory = ZepGraphMemory(
        client=client,
        graph_uuid="graph-1",
        search_filters=SearchFilters(node_labels=["ProgrammingLanguage"]),
    )

    result = await memory.query("Python", scope="nodes", limit=3)

    client.graph.search_nodes.assert_awaited_once()
    kwargs = client.graph.search_nodes.call_args.kwargs
    assert kwargs["query"] == "Python"
    assert kwargs["page_size"] == 3
    assert isinstance(kwargs["filters"], NodeSearchFilters)
    assert kwargs["filters"].node_labels == ["ProgrammingLanguage"]
    assert result.results[0].content == "Python:\n A language"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "item", "expected_content", "expected_metadata"),
    [
        (
            "edges",
            SimpleNamespace(
                fact="Alice likes hiking",
                name="LIKES",
                attributes={"since": 2020},
                created_at="now",
                valid_at="then",
                invalid_at=None,
                expired_at=None,
            ),
            "Alice likes hiking",
            {"edge_name": "LIKES", "edge_attributes": {"since": 2020}, "created_at": "now"},
        ),
        (
            "nodes",
            SimpleNamespace(
                name="Alice",
                summary="A hiker",
                attributes={"city": "Portland"},
                created_at="now",
            ),
            "Alice:\n A hiker",
            {"node_name": "Alice", "node_attributes": {"city": "Portland"}, "created_at": "now"},
        ),
        (
            "episodes",
            SimpleNamespace(
                content="Alice said she likes hiking",
                source="message",
                role="user",
                role_name="Alice",
                created_at="now",
            ),
            "Alice said she likes hiking",
            {
                "episode_type": "message",
                "episode_role": "user",
                "episode_name": "Alice",
                "created_at": "now",
            },
        ),
        (
            "observations",
            SimpleNamespace(
                name="hiking",
                summary="Alice's hobby",
                attributes={"category": "hobby"},
                created_at="now",
            ),
            "hiking: Alice's hobby",
            {"observation_name": "hiking", "observation_attributes": {"category": "hobby"}},
        ),
        (
            "thread_summaries",
            SimpleNamespace(summary="Alice likes hiking", thread_uuid="thread-1", created_at="now"),
            "Alice likes hiking",
            {"thread_uuid": "thread-1", "created_at": "now"},
        ),
    ],
)
async def test_query_maps_scope_results(
    scope: str,
    item: SimpleNamespace,
    expected_content: str,
    expected_metadata: dict[str, object],
) -> None:
    client = _make_client()
    method_names = {
        "edges": "search_edges",
        "nodes": "search_nodes",
        "episodes": "search_episodes",
        "observations": "search_observations",
        "thread_summaries": "search_thread_summaries",
    }
    getattr(client.graph, method_names[scope]).return_value = SimpleNamespace(items=[item])
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")

    result = await memory.query("query", scope=scope)

    assert result.results[0].content == expected_content
    assert result.results[0].metadata["source"] == "graph"
    for key, value in expected_metadata.items():
        assert result.results[0].metadata[key] == value


@pytest.mark.asyncio
async def test_query_auto_uses_context_and_character_limit() -> None:
    client = _make_client()
    filters = SearchFilters(node_labels=["Framework"])
    memory = ZepGraphMemory(
        client=client,
        graph_uuid="graph-1",
        search_filters=filters,
        max_characters=1500,
    )

    result = await memory.query("frameworks", scope="auto")

    client.graph.get_context.assert_awaited_once_with(
        "graph-1",
        query="frameworks",
        max_characters=1500,
        filters=filters,
    )
    assert result.results[0].content == "Graph context"


@pytest.mark.asyncio
async def test_query_error_returns_empty_results() -> None:
    client = _make_client()
    client.graph.search_edges.side_effect = RuntimeError("unavailable")
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")

    result = await memory.query("query")

    assert result.results == []


@pytest.mark.asyncio
async def test_update_context_uses_two_recent_episodes_and_graph_context() -> None:
    client = _make_client()
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1", max_characters=1200)
    model_context = await _context_with_message()

    result = await memory.update_context(model_context)

    client.graph.episode.list.assert_awaited_once_with(
        "graph-1",
        page_size=2,
        order="desc",
    )
    client.graph.get_context.assert_awaited_once_with(
        "graph-1",
        query="Latest episode\nEarlier episode",
        max_characters=1200,
    )
    assert result.memories.results[0].content == "Graph context"
    messages = await model_context.get_messages()
    assert any(getattr(message, "content", None) == "Graph context" for message in messages)


@pytest.mark.asyncio
async def test_update_context_with_empty_episode_list_returns_no_memory() -> None:
    client = _make_client()
    client.graph.episode.list.return_value = SimpleNamespace(items=[])
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")
    model_context = await _context_with_message()

    result = await memory.update_context(model_context)

    assert result.memories.results == []
    client.graph.get_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_context_error_degrades_to_empty_memory() -> None:
    client = _make_client()
    client.graph.episode.list.side_effect = RuntimeError("unavailable")
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")
    model_context = await _context_with_message()

    result = await memory.update_context(model_context)

    assert result.memories.results == []


@pytest.mark.asyncio
async def test_clear_deletes_the_shared_graph() -> None:
    client = _make_client()
    memory = ZepGraphMemory(client=client, graph_uuid="graph-1")

    await memory.clear()

    client.graph.delete.assert_awaited_once_with("graph-1")
