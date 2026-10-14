from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import UserMessage
from zep_cloud.client import AsyncZep

from zep_autogen import ZepUserMemory


def _make_client() -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.user = MagicMock()
    client.user.create = AsyncMock()
    client.thread = MagicMock()
    client.thread.create = AsyncMock(return_value=SimpleNamespace(uuid_="thread-1"))
    client.thread.add_messages = AsyncMock()
    client.thread.get_context = AsyncMock(return_value=SimpleNamespace(context="stored context"))
    client.thread.list_messages = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = AsyncMock()
    client.graph.search_edges = AsyncMock(
        return_value=SimpleNamespace(items=[SimpleNamespace(fact="Alice likes hiking.")])
    )
    client.graph.search_nodes = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.search_episodes = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.search_observations = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.search_thread_summaries = AsyncMock(return_value=SimpleNamespace(items=[]))
    client.graph.get_context = AsyncMock(return_value=SimpleNamespace(context="auto context"))
    return client


async def _context_with_message(text: str) -> UnboundedChatCompletionContext:
    context = UnboundedChatCompletionContext()
    await context.add_message(UserMessage(content=text, source="user"))
    return context


def test_package_exports_user_memory() -> None:
    from zep_autogen import ZepUserMemory as ExportedMemory

    assert ExportedMemory is ZepUserMemory


def test_memory_stores_uuid_scope() -> None:
    memory = ZepUserMemory(client=_make_client(), user_uuid="user-1", thread_uuid="thread-1")

    assert memory.user_uuid == "user-1"
    assert memory.thread_uuid == "thread-1"
    assert memory.graph_uuid == "user-1"


def test_memory_rejects_empty_user_uuid() -> None:
    with pytest.raises(ValueError, match="user_uuid is required"):
        ZepUserMemory(client=_make_client(), user_uuid="")


def test_memory_rejects_non_sdk_client() -> None:
    with pytest.raises(TypeError, match="AsyncZep"):
        ZepUserMemory(client="not-a-client", user_uuid="user-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_add_persists_message_to_existing_thread() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1", thread_uuid="thread-1")
    content = MemoryContent(
        content="Hello",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "user", "name": "Alice"},
    )

    await memory.add(content)

    client.thread.add_messages.assert_awaited_once()
    assert client.thread.add_messages.call_args.kwargs["thread_uuid"] == "thread-1"
    message = client.thread.add_messages.call_args.kwargs["messages"][0]
    assert (message.role, message.name, message.content) == ("user", "Alice", "Hello")


@pytest.mark.asyncio
async def test_add_creates_thread_when_thread_uuid_is_omitted() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1")
    content = MemoryContent(
        content="Hello",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "message", "role": "assistant"},
    )

    await memory.add(content)

    client.thread.create.assert_awaited_once_with(user_uuid="user-1")
    client.user.create.assert_not_awaited()
    assert client.thread.add_messages.call_args.kwargs["thread_uuid"] == "thread-1"


@pytest.mark.asyncio
async def test_add_graph_data_uses_user_graph_uuid() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1", graph_uuid="graph-1")
    content = MemoryContent(
        content="A graph fact",
        mime_type=MemoryMimeType.TEXT,
        metadata={"type": "data"},
    )

    await memory.add(content)

    client.graph.episode.add.assert_awaited_once_with(
        "graph-1",
        type="text",
        data="A graph fact",
    )


@pytest.mark.asyncio
async def test_add_json_graph_data_preserves_json_type() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1")
    content = MemoryContent(
        content='{"topic":"zep"}',
        mime_type=MemoryMimeType.JSON,
        metadata={"type": "data"},
    )

    await memory.add(content)

    assert client.graph.episode.add.call_args.kwargs["type"] == "json"


@pytest.mark.asyncio
async def test_add_rejects_unsupported_memory_type() -> None:
    memory = ZepUserMemory(client=_make_client(), user_uuid="user-1")
    content = MemoryContent(
        content="unsupported",
        mime_type=MemoryMimeType.IMAGE,
        metadata={"type": "data"},
    )

    with pytest.raises(ValueError, match="Unsupported mime type"):
        await memory.add(content)


@pytest.mark.asyncio
async def test_query_maps_search_results_to_memory_content() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1")

    result = await memory.query("What does Alice like?")

    assert len(result.results) == 1
    assert result.results[0].content == "Alice likes hiking."
    client.graph.search_edges.assert_awaited_once()
    assert client.graph.search_edges.call_args.args == ("user-1",)


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
            {"edge_name": "LIKES", "edge_attributes": {"since": 2020}, "valid_at": "then"},
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
            {"node_name": "Alice", "node_attributes": {"city": "Portland"}},
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
async def test_query_maps_each_scope_result(
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
    memory = ZepUserMemory(client=client, user_uuid="user-1")

    result = await memory.query("query", scope=scope)

    assert result.results[0].content == expected_content
    assert result.results[0].metadata["source"] == "user_graph"
    for key, value in expected_metadata.items():
        assert result.results[0].metadata[key] == value


@pytest.mark.asyncio
async def test_query_auto_uses_graph_context() -> None:
    client = _make_client()
    memory = ZepUserMemory(client=client, user_uuid="user-1")

    result = await memory.query("query", scope="auto")

    assert result.results[0].content == "auto context"
    client.graph.get_context.assert_awaited_once_with(
        "user-1",
        query="query",
    )


@pytest.mark.asyncio
async def test_query_failure_returns_empty_results() -> None:
    client = _make_client()
    client.graph.search_edges.side_effect = RuntimeError("unavailable")
    memory = ZepUserMemory(client=client, user_uuid="user-1")

    result = await memory.query("query")

    assert result.results == []


@pytest.mark.asyncio
async def test_update_context_uses_v4_context_and_message_paging() -> None:
    client = _make_client()
    client.thread.list_messages.return_value = SimpleNamespace(
        items=[SimpleNamespace(name="Alice", role="user", content="Hello")]
    )
    memory = ZepUserMemory(
        client=client,
        user_uuid="user-1",
        thread_uuid="thread-1",
        context_template_uuid="template-1",
    )
    model_context = await _context_with_message("hello")

    result = await memory.update_context(model_context)

    client.thread.get_context.assert_awaited_once_with("thread-1", template_uuid="template-1")
    client.thread.list_messages.assert_awaited_once_with("thread-1", page_size=10)
    assert len(result.memories.results) == 1
    messages = await model_context.get_messages()
    assert any(
        getattr(message, "content", "").find("stored context") >= 0
        for message in messages
        if isinstance(getattr(message, "content", None), str)
    )


@pytest.mark.asyncio
async def test_update_context_failure_degrades_to_empty_result() -> None:
    client = _make_client()
    client.thread.get_context.side_effect = RuntimeError("unavailable")
    memory = ZepUserMemory(client=client, user_uuid="user-1", thread_uuid="thread-1")
    model_context = await _context_with_message("hello")

    result = await memory.update_context(model_context)

    assert result.memories.results == []
