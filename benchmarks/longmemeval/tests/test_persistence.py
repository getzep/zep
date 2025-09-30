"""Tests for results persistence"""

import json

import pytest

from common import BenchmarkMetrics, EvaluationResult
from config import BenchmarkConfig, GraphParams, ModelConfig
from persistence import ResultsPersistence


class TestResultsPersistence:
    @pytest.fixture
    def sample_config(self):
        return BenchmarkConfig(
            graph_params=GraphParams(),
            models=ModelConfig(),
        )

    @pytest.fixture
    def sample_metrics(self):
        return BenchmarkMetrics(
            accuracy=0.8,
            correct_count=80,
            total_count=100,
            avg_response_duration=2.0,
            avg_retrieval_duration=0.5,
        )

    @pytest.fixture
    def sample_results(self):
        return [
            EvaluationResult(
                user_id="user1",
                question_id="q1",
                question="Test question",
                question_type="temporal-reasoning",
                hypothesis="Test answer",
                gold_answer="Gold answer",
                context="Test context",
                context_tokens=10,
                context_chars=100,
                duration=1.0,
                grade=True,
            )
        ]

    def test_save_run(self, tmp_path, sample_config, sample_metrics, sample_results):
        """Test saving a complete benchmark run"""
        # Create temporary config file
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: config")

        # Create persistence with temp directory
        persistence = ResultsPersistence(tmp_path / "experiments")

        # Save run
        run_dir = persistence.save_run(
            sample_config,
            sample_metrics,
            sample_results,
            config_file,
        )

        # Verify directory was created
        assert run_dir.exists()
        assert run_dir.name.startswith("run_")

        # Verify results.json was created and valid
        results_path = run_dir / "results.json"
        assert results_path.exists()

        with open(results_path) as f:
            data = json.load(f)

        assert "config" in data
        assert "metrics" in data
        assert "results" in data
        assert data["metrics"]["accuracy"] == 0.8
        assert len(data["results"]) == 1

        # Verify config snapshot was created
        config_snapshot = run_dir / "config.yaml"
        assert config_snapshot.exists()
        assert config_snapshot.read_text() == "test: config"

    def test_experiments_dir_creation(self, tmp_path):
        """Test that experiments directory is created if it doesn't exist"""
        exp_dir = tmp_path / "new_experiments"
        persistence = ResultsPersistence(exp_dir)

        assert not exp_dir.exists()  # Shouldn't create until save

        # Create dummy data
        config = BenchmarkConfig(graph_params=GraphParams(), models=ModelConfig())
        metrics = BenchmarkMetrics(
            accuracy=0.5,
            correct_count=50,
            total_count=100,
            avg_response_duration=1.0,
            avg_retrieval_duration=0.5,
        )
        results = []

        config_file = tmp_path / "config.yaml"
        config_file.write_text("test")

        run_dir = persistence.save_run(config, metrics, results, config_file)

        assert exp_dir.exists()
        assert run_dir.parent == exp_dir
