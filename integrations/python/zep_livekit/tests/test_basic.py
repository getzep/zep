"""
Basic tests for Zep LiveKit integration.

These tests cover package imports, agent initialization, and basic functionality
using mocked Zep clients to avoid requiring API keys.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from livekit import agents
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message, MemorySearchResult

from zep_livekit import ZepMemoryAgent, ZepLiveKitError
from zep_livekit.exceptions import AgentConfigurationError, MemoryStorageError, MemoryRetrievalError
from zep_livekit.memory import MemoryManager, format_memory_context


class TestPackageStructure:
    """Test basic package structure and imports."""

    def test_package_imports(self):
        """Test that package imports work correctly."""
        import zep_livekit
        
        assert hasattr(zep_livekit, 'ZepMemoryAgent')
        assert hasattr(zep_livekit, 'ZepLiveKitError')
        assert hasattr(zep_livekit, '__version__')
        
    def test_version_accessible(self):
        """Test that version is accessible."""
        import zep_livekit
        
        assert isinstance(zep_livekit.__version__, str)
        assert len(zep_livekit.__version__) > 0


class TestZepMemoryAgent:
    """Test ZepMemoryAgent functionality with mocked clients."""

    def create_mock_zep_client(self):
        """Create a mock Zep client for testing."""
        mock_client = MagicMock(spec=AsyncZep)
        
        # Mock thread operations
        mock_client.thread = MagicMock()
        mock_client.thread.create = AsyncMock()
        mock_client.thread.get = AsyncMock()
        mock_client.thread.add_messages = AsyncMock()
        mock_client.thread.delete = AsyncMock()
        mock_client.thread.get_user_context = AsyncMock()
        
        # Mock graph operations  
        mock_client.graph = MagicMock()
        mock_client.graph.add = AsyncMock()
        mock_client.graph.search = AsyncMock()
        
        return mock_client

    def test_agent_initialization_valid(self):
        """Test successful agent initialization."""
        mock_client = self.create_mock_zep_client()
        
        agent = ZepMemoryAgent(
            zep_client=mock_client,
            user_id="test_user",
            thread_id="test_thread"
        )
        
        assert agent.user_id == "test_user"
        assert agent.thread_id == "test_thread"
        assert agent._zep_client == mock_client

    def test_agent_initialization_no_user_id(self):
        """Test agent initialization fails without user_id."""
        mock_client = self.create_mock_zep_client()
        
        with pytest.raises(AgentConfigurationError, match="user_id is required"):
            ZepMemoryAgent(zep_client=mock_client, user_id="", thread_id="test_thread")

    def test_agent_initialization_no_thread_id(self):
        """Test agent initialization fails without thread_id."""
        mock_client = self.create_mock_zep_client()
        
        with pytest.raises(AgentConfigurationError, match="thread_id is required"):
            ZepMemoryAgent(zep_client=mock_client, user_id="test_user", thread_id="")

    def test_agent_initialization_invalid_client(self):
        """Test agent initialization fails with invalid client."""
        with pytest.raises(AgentConfigurationError, match="must be an instance of AsyncZep"):
            ZepMemoryAgent(zep_client="not_a_client", user_id="test_user", thread_id="test_thread")

    @pytest.mark.asyncio
    async def test_on_enter(self):
        """Test on_enter logs entry."""
        mock_client = self.create_mock_zep_client()
        
        agent = ZepMemoryAgent(
            zep_client=mock_client,
            user_id="test_user",
            thread_id="test_thread"
        )
        
        await agent.on_enter()
        
        # Should not create or verify threads (that's handled externally now)
        mock_client.thread.create.assert_not_called()
        mock_client.thread.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_user_turn_completed_stores_messages(self):
        """Test that user turn completion stores messages."""
        mock_client = self.create_mock_zep_client()
        
        agent = ZepMemoryAgent(
            zep_client=mock_client,
            user_id="test_user",
            thread_id="test_thread"
        )
        
        # Mock chat context with messages
        mock_chat_ctx = MagicMock(spec=agents.ChatContext)
        mock_messages = [
            agents.ChatMessage.create(text="Hello", role="user"),
            agents.ChatMessage.create(text="Hi there!", role="assistant")
        ]
        mock_chat_ctx.get_messages = AsyncMock(return_value=mock_messages)
        
        await agent.on_user_turn_completed(mock_chat_ctx)
        
        # Should store messages in thread
        mock_client.thread.add_messages.assert_called_once()
        call_args = mock_client.thread.add_messages.call_args
        assert call_args[1]['thread_id'] == "test_thread"
        
        # Should add user message to graph
        mock_client.graph.add.assert_called_once()
        graph_call_args = mock_client.graph.add.call_args
        assert graph_call_args[1]['user_id'] == "test_user"
        assert graph_call_args[1]['type'] == "text"

    @pytest.mark.asyncio
    async def test_update_chat_ctx_with_memory(self):
        """Test chat context update with memory injection."""
        mock_client = self.create_mock_zep_client()
        
        # Mock memory responses
        mock_memory_result = MagicMock()
        mock_memory_result.context = "Previous context"
        mock_memory_result.messages = [
            MagicMock(role="user", content="Previous message"),
        ]
        mock_client.thread.get_user_context.return_value = mock_memory_result
        
        mock_graph_result = MagicMock()
        mock_graph_result.edges = [MagicMock(fact="User likes coffee")]
        mock_graph_result.episodes = [MagicMock(content="Past conversation")]
        mock_graph_result.nodes = []
        mock_client.graph.search.return_value = mock_graph_result
        
        agent = ZepMemoryAgent(
            zep_client=mock_client,
            user_id="test_user",
            thread_id="test_thread"
        )
        
        # Mock chat context
        mock_chat_ctx = MagicMock(spec=agents.ChatContext)
        mock_messages = [agents.ChatMessage.create(text="What do I like?", role="user")]
        mock_chat_ctx.get_messages = AsyncMock(return_value=mock_messages)
        mock_chat_ctx.add_message = AsyncMock()
        
        await agent.update_chat_ctx(mock_chat_ctx)
        
        # Should search for relevant memories
        mock_client.graph.search.assert_called_once_with(
            user_id="test_user",
            query="What do I like?",
            limit=3
        )
        
        # Should add system message with memory context
        mock_chat_ctx.add_message.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_clear_memory(self):
        """Test memory clearing functionality."""
        mock_client = self.create_mock_zep_client()
        
        agent = ZepMemoryAgent(
            zep_client=mock_client,
            user_id="test_user",
            thread_id="test_thread"
        )
        
        await agent.clear_memory()
        
        # Should delete the thread
        mock_client.thread.delete.assert_called_once_with(thread_id="test_thread")


class TestMemoryManager:
    """Test MemoryManager utility functionality."""
    
    def create_mock_client(self):
        """Create a mock client for testing."""
        mock_client = MagicMock(spec=AsyncZep)
        mock_client.thread = MagicMock()
        mock_client.graph = MagicMock()
        return mock_client

    @pytest.mark.asyncio
    async def test_create_thread_if_not_exists_new(self):
        """Test creating a new thread."""
        mock_client = self.create_mock_client()
        mock_client.thread.get = AsyncMock(side_effect=Exception("Thread not found"))
        mock_client.thread.create = AsyncMock()
        
        manager = MemoryManager(mock_client, "test_user")
        result = await manager.create_thread_if_not_exists("new_thread")
        
        assert result is True
        mock_client.thread.create.assert_called_once_with(
            thread_id="new_thread", 
            user_id="test_user"
        )

    @pytest.mark.asyncio
    async def test_create_thread_if_not_exists_existing(self):
        """Test handling existing thread."""
        mock_client = self.create_mock_client()
        mock_client.thread.get = AsyncMock()  # No exception = thread exists
        
        manager = MemoryManager(mock_client, "test_user")
        result = await manager.create_thread_if_not_exists("existing_thread")
        
        assert result is False
        mock_client.thread.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_conversation_batch(self):
        """Test batch conversation storage."""
        mock_client = self.create_mock_client()
        mock_client.thread.add_messages = AsyncMock()
        
        manager = MemoryManager(mock_client, "test_user")
        
        messages = [
            {"content": "Hello", "role": "user"},
            {"content": "Hi!", "role": "assistant"},
        ]
        
        result = await manager.store_conversation_batch("test_thread", messages)
        
        assert result == 2
        mock_client.thread.add_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_relevant_memories(self):
        """Test memory search functionality."""
        mock_client = self.create_mock_client()
        
        # Mock graph search
        mock_graph_result = MagicMock()
        mock_graph_result.edges = [MagicMock(fact="Test fact")]
        mock_graph_result.episodes = [MagicMock(content="Test episode")]
        mock_graph_result.nodes = [MagicMock(name="Entity", summary="Test entity")]
        mock_client.graph.search = AsyncMock(return_value=mock_graph_result)
        
        # Mock thread context
        mock_memory_result = MagicMock()
        mock_memory_result.context = "Thread context"
        mock_client.thread.get_user_context = AsyncMock(return_value=mock_memory_result)
        
        manager = MemoryManager(mock_client, "test_user")
        result = await manager.search_relevant_memories(
            "test query", 
            thread_id="test_thread"
        )
        
        assert "facts" in result
        assert "episodes" in result
        assert "nodes" in result
        assert "thread_context" in result
        assert len(result["facts"]) == 1
        assert result["thread_context"] == "Thread context"


class TestUtilities:
    """Test utility functions."""
    
    def test_format_memory_context_empty(self):
        """Test formatting empty memory context."""
        result = format_memory_context([], [], [], "")
        assert result == ""
    
    def test_format_memory_context_with_data(self):
        """Test formatting memory context with data."""
        facts = ["User likes coffee", "User works remotely"]
        episodes = ["Previous conversation about work"]
        nodes = ["Coffee: beverage preference"]
        thread_context = "Recent discussion"
        
        result = format_memory_context(facts, episodes, nodes, thread_context)
        
        assert "Recent discussion" in result
        assert "User likes coffee" in result
        assert "Previous conversation" in result
        assert "Coffee: beverage" in result
        
    def test_format_memory_context_max_items(self):
        """Test max items limiting in memory context."""
        facts = ["Fact 1", "Fact 2", "Fact 3", "Fact 4"]
        
        result = format_memory_context(facts, [], [], "", max_items=2)
        
        assert "Fact 1" in result
        assert "Fact 2" in result
        assert "Fact 3" not in result
        assert "Fact 4" not in result


if __name__ == "__main__":
    pytest.main([__file__])