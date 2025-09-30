"""Integration tests with mocked external services"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from evaluation import EvaluationRunner


class TestEvaluationIntegration:
    """Integration tests for the evaluation flow"""

    @pytest.fixture
    def mock_zep_client(self):
        """Mock Zep client with graph search responses"""
        client = MagicMock()

        # Mock edge search response
        edges_response = SimpleNamespace(
            edges=[
                MagicMock(valid_at="2024-01-01", fact="User likes pizza"),
            ]
        )

        # Mock node search response
        nodes_response = SimpleNamespace(
            nodes=[
                MagicMock(name="John", labels=["Person"], summary="Software engineer"),
            ]
        )

        # Mock episode search response
        episodes_response = SimpleNamespace(
            episodes=[
                MagicMock(content="Previous conversation"),
            ]
        )

        client.graph.search = AsyncMock(side_effect=[
            edges_response,
            nodes_response,
            episodes_response,
        ])

        return client

    @pytest.fixture
    def mock_openai_client(self):
        """Mock OpenAI client with responses"""
        client = MagicMock()

        # Mock response generation
        response_completion = MagicMock()
        response_completion.choices = [
            MagicMock(message=MagicMock(content="Pizza is delicious"))
        ]
        client.chat.completions.create = AsyncMock(return_value=response_completion)

        # Mock grading
        grade_result = MagicMock()
        grade_result.choices = [
            MagicMock(message=MagicMock(parsed=MagicMock(is_correct="yes")))
        ]
        client.beta.chat.completions.parse = AsyncMock(return_value=grade_result)

        return client

    @pytest.fixture
    def sample_dataframe(self):
        """Create a sample evaluation dataframe"""
        return pd.DataFrame({
            "question_id": ["q1"],
            "question_type": ["temporal-reasoning"],
            "question": ["What does the user like?"],
            "question_date": ["2024-01-15"],
            "answer": ["Pizza"],
        })

    @pytest.mark.asyncio
    async def test_evaluate_conversation_flow(
        self,
        monkeypatch,
        mock_zep_client,
        mock_openai_client,
        sample_dataframe,
    ):
        """Test the complete evaluation flow with mocked services"""
        # Patch client factories
        monkeypatch.setattr(
            "evaluation.create_zep_client",
            lambda **kwargs: mock_zep_client,
        )
        monkeypatch.setattr(
            "evaluation.create_openai_client",
            lambda **kwargs: mock_openai_client,
        )

        # Create runner
        from config import BenchmarkConfig, GraphParams, ModelConfig

        config = BenchmarkConfig(
            graph_params=GraphParams(
                edge_limit=10,
                node_limit=5,
                episode_limit=5,
            ),
            models=ModelConfig(
                response_model="gpt-4o",
                grader_model="gpt-4o",
            ),
        )
        runner = EvaluationRunner(config=config)

        # Run evaluation
        result, correct, duration, retrieval_duration = await runner.evaluate_conversation(
            sample_dataframe, 0
        )

        # Verify result structure
        assert result.user_id == "lme_s_experiment_user_0"
        assert result.question_id == "q1"
        assert result.hypothesis == "Pizza is delicious"
        assert result.grade is True
        assert correct == 1
        assert duration > 0
        assert retrieval_duration > 0

        # Verify context was composed
        assert "User likes pizza" in result.context
        assert "John" in result.context

        # Verify clients were called correctly
        assert mock_zep_client.graph.search.call_count == 3
        assert mock_openai_client.chat.completions.create.called
        assert mock_openai_client.beta.chat.completions.parse.called

    @pytest.mark.asyncio
    async def test_evaluate_with_no_episodes(
        self,
        monkeypatch,
        mock_zep_client,
        mock_openai_client,
        sample_dataframe,
    ):
        """Test evaluation with episodes disabled"""
        monkeypatch.setattr(
            "evaluation.create_zep_client",
            lambda **kwargs: mock_zep_client,
        )
        monkeypatch.setattr(
            "evaluation.create_openai_client",
            lambda **kwargs: mock_openai_client,
        )

        # Create runner with episodes disabled
        from config import BenchmarkConfig, GraphParams, ModelConfig

        config = BenchmarkConfig(
            graph_params=GraphParams(
                edge_limit=10,
                node_limit=5,
                episode_limit=0,  # Disabled
            ),
            models=ModelConfig(
                response_model="gpt-4o",
                grader_model="gpt-4o",
            ),
        )
        runner = EvaluationRunner(config=config)

        result, _, _, _ = await runner.evaluate_conversation(sample_dataframe, 0)

        # Should only call graph search twice (edges and nodes, not episodes)
        assert mock_zep_client.graph.search.call_count == 2
        assert result.grade is True
