"""Tests for evaluation module"""

from unittest.mock import MagicMock

import pytest
from zep_cloud import EntityEdge, EntityNode, Episode

from evaluation import EvaluationRunner


class TestComposeContext:
    """Test pure context composition function"""

    @pytest.fixture
    def runner(self, monkeypatch):
        """Create evaluation runner with mocked clients"""
        # Mock the client factories to avoid needing real API keys
        mock_zep = MagicMock()
        mock_oai = MagicMock()

        monkeypatch.setattr("evaluation.create_zep_client", lambda **kwargs: mock_zep)
        monkeypatch.setattr("evaluation.create_openai_client", lambda **kwargs: mock_oai)

        return EvaluationRunner()

    def test_compose_context_with_edges(self, runner):
        edges = [
            MagicMock(spec=EntityEdge, valid_at="2024-01-01", fact="User likes pizza"),
            MagicMock(spec=EntityEdge, valid_at="2024-01-02", fact="User visited Rome"),
        ]
        nodes = []
        episodes = []

        context = runner.compose_context(edges, nodes, episodes)

        assert "User likes pizza" in context
        assert "User visited Rome" in context
        assert "2024-01-01" in context
        assert "2024-01-02" in context

    def test_compose_context_with_nodes(self, runner):
        edges = []
        node = MagicMock(spec=EntityNode)
        node.name = "John"
        node.labels = ["Person"]
        node.summary = "A software engineer"
        nodes = [node]
        episodes = []

        context = runner.compose_context(edges, nodes, episodes)

        assert "John" in context
        assert "Person" in context
        assert "A software engineer" in context

    def test_compose_context_with_episodes(self, runner):
        edges = []
        nodes = []
        episodes = [
            MagicMock(spec=Episode, content="Message 1: Hello"),
            MagicMock(spec=Episode, content="Message 2: How are you?"),
        ]

        context = runner.compose_context(edges, nodes, episodes)

        assert "Message 1: Hello" in context
        assert "Message 2: How are you?" in context

    def test_compose_context_empty(self, runner):
        context = runner.compose_context([], [], [])

        # Should still have template structure
        assert "FACTS" in context
        assert "ENTITIES" in context
        assert "MESSAGES" in context

    def test_compose_context_edge_without_date(self, runner):
        edges = [
            MagicMock(spec=EntityEdge, valid_at=None, fact="Undated fact"),
        ]

        context = runner.compose_context(edges, [], [])

        assert "Undated fact" in context
        assert "date unknown" in context


class TestTokenCounting:
    """Test token counting functionality"""

    @pytest.fixture
    def runner(self, monkeypatch):
        """Create evaluation runner with mocked clients"""
        mock_zep = MagicMock()
        mock_oai = MagicMock()

        monkeypatch.setattr("evaluation.create_zep_client", lambda **kwargs: mock_zep)
        monkeypatch.setattr("evaluation.create_openai_client", lambda **kwargs: mock_oai)

        return EvaluationRunner()

    def test_count_tokens_basic(self, runner):
        text = "This is a test string"
        count = runner._count_tokens(text)
        assert count > 0
        assert isinstance(count, int)

    def test_count_tokens_empty(self, runner):
        count = runner._count_tokens("")
        assert count == 0


class TestReasoningModelDetection:
    """Test reasoning model detection and parameter building"""

    @pytest.fixture
    def runner(self, monkeypatch):
        """Create evaluation runner with mocked clients"""
        mock_zep = MagicMock()
        mock_oai = MagicMock()

        monkeypatch.setattr("evaluation.create_zep_client", lambda **kwargs: mock_zep)
        monkeypatch.setattr("evaluation.create_openai_client", lambda **kwargs: mock_oai)

        return EvaluationRunner()

    def test_is_reasoning_model_gpt5(self, runner):
        assert runner._is_reasoning_model("gpt-5") is True
        assert runner._is_reasoning_model("GPT-5-preview") is True

    def test_is_reasoning_model_o1(self, runner):
        assert runner._is_reasoning_model("o1") is True
        assert runner._is_reasoning_model("o1-preview") is True
        assert runner._is_reasoning_model("o1-mini") is True

    def test_is_reasoning_model_o3(self, runner):
        assert runner._is_reasoning_model("o3") is True
        assert runner._is_reasoning_model("o3-mini") is True

    def test_is_not_reasoning_model(self, runner):
        assert runner._is_reasoning_model("gpt-4o") is False
        assert runner._is_reasoning_model("gpt-4.1") is False
        assert runner._is_reasoning_model("gpt-3.5-turbo") is False

    def test_build_completion_params_traditional(self, monkeypatch):
        """Test parameter building for traditional models"""
        from config import BenchmarkConfig, GraphParams, ModelConfig

        mock_zep = MagicMock()
        mock_oai = MagicMock()

        monkeypatch.setattr("evaluation.create_zep_client", lambda **kwargs: mock_zep)
        monkeypatch.setattr("evaluation.create_openai_client", lambda **kwargs: mock_oai)

        config = BenchmarkConfig(
            graph_params=GraphParams(),
            models=ModelConfig(
                response_model="gpt-4o",
                temperature=0.5,
                max_tokens=2000,
            ),
        )
        runner = EvaluationRunner(config=config)

        messages = [{"role": "user", "content": "test"}]
        params = runner._build_completion_params("gpt-4o", messages)

        assert params["model"] == "gpt-4o"
        assert params["messages"] == messages
        assert params["temperature"] == 0.5
        assert params["max_tokens"] == 2000
        assert "reasoning_effort" not in params

    def test_build_completion_params_reasoning(self, monkeypatch):
        """Test parameter building for reasoning models"""
        from config import BenchmarkConfig, GraphParams, ModelConfig

        mock_zep = MagicMock()
        mock_oai = MagicMock()

        monkeypatch.setattr("evaluation.create_zep_client", lambda **kwargs: mock_zep)
        monkeypatch.setattr("evaluation.create_openai_client", lambda **kwargs: mock_oai)

        config = BenchmarkConfig(
            graph_params=GraphParams(),
            models=ModelConfig(
                response_model="gpt-5",
                reasoning_effort="high",
                max_completion_tokens=10000,
            ),
        )
        runner = EvaluationRunner(config=config)

        messages = [{"role": "user", "content": "test"}]
        params = runner._build_completion_params("gpt-5", messages)

        assert params["model"] == "gpt-5"
        assert params["messages"] == messages
        assert params["reasoning_effort"] == "high"
        assert params["max_completion_tokens"] == 10000
        assert "temperature" not in params
        assert "max_tokens" not in params
