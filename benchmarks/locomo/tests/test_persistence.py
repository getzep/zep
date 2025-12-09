"""Tests for results persistence."""

import json
import logging

import pytest

from common import EvaluationResult
from config import BenchmarkConfig
from persistence import ResultsPersistence


@pytest.fixture
def sample_results():
    """Create sample evaluation results."""
    return [
        EvaluationResult(
            user_id="user_1",
            test_id="test_1",
            category="navigation",
            difficulty="easy",
            query="test query 1",
            golden_answer="answer 1",
            hypothesis="hypothesis 1",
            context="context 1",
            context_tokens=100,
            context_chars=500,
            retrieval_duration=0.5,
            response_duration=1.0,
            total_duration=1.5,
            grade=True,
        ),
        EvaluationResult(
            user_id="user_1",
            test_id="test_2",
            category="navigation",
            difficulty="medium",
            query="test query 2",
            golden_answer="answer 2",
            hypothesis="hypothesis 2",
            context="context 2",
            context_tokens=150,
            context_chars=750,
            retrieval_duration=0.7,
            response_duration=1.2,
            total_duration=1.9,
            grade=False,
        ),
        EvaluationResult(
            user_id="user_2",
            test_id="test_3",
            category="media",
            difficulty="hard",
            query="test query 3",
            golden_answer="answer 3",
            hypothesis="hypothesis 3",
            context="context 3",
            context_tokens=200,
            context_chars=1000,
            retrieval_duration=0.9,
            response_duration=1.5,
            total_duration=2.4,
            grade=True,
        ),
    ]


def test_save_run(tmp_path, sample_results):
    """Test saving evaluation run."""
    config = BenchmarkConfig()
    logger = logging.getLogger("test")

    # Override experiments directory for testing
    persistence = ResultsPersistence(config, logger)
    persistence.experiments_dir = tmp_path

    # Save run
    run_dir = persistence.save_run(sample_results)

    assert run_dir.exists()
    assert (run_dir / "results.json").exists()
    assert (run_dir / "config.yaml").exists()

    # Verify results.json content
    with open(run_dir / "results.json") as f:
        data = json.load(f)

    assert data["dataset"] == "locomo"
    assert len(data["results"]) == 3
    assert data["metrics"]["accuracy"] == pytest.approx(2 / 3)
    assert data["metrics"]["correct_count"] == 2
    assert data["metrics"]["total_count"] == 3


def test_calculate_metrics(tmp_path, sample_results):
    """Test metrics calculation."""
    config = BenchmarkConfig()
    logger = logging.getLogger("test")
    persistence = ResultsPersistence(config, logger)

    metrics = persistence._calculate_metrics(sample_results)

    # Overall metrics
    assert metrics.accuracy == pytest.approx(2 / 3)
    assert metrics.correct_count == 2
    assert metrics.total_count == 3

    # Latency stats
    assert metrics.retrieval_duration_stats.median > 0
    assert metrics.response_duration_stats.mean > 0
    assert metrics.total_duration_stats.max > 0

    # Token stats
    assert metrics.context_token_stats.median > 0
    assert metrics.context_char_stats.mean > 0

    # Category breakdown
    assert len(metrics.by_category) == 2  # navigation and media
    nav_category = next(c for c in metrics.by_category if c.category == "navigation")
    assert nav_category.total_count == 2
    assert nav_category.correct_count == 1

    # Difficulty breakdown
    assert len(metrics.by_difficulty) == 3  # easy, medium, hard
    easy_diff = next(d for d in metrics.by_difficulty if d.difficulty == "easy")
    assert easy_diff.total_count == 1
    assert easy_diff.accuracy == 1.0

    # Telemetry breakdown


def test_list_runs(tmp_path):
    """Test listing available runs."""
    config = BenchmarkConfig()
    logger = logging.getLogger("test")
    persistence = ResultsPersistence(config, logger)
    persistence.experiments_dir = tmp_path

    # Create mock run directories
    (tmp_path / "run_20240101_120000").mkdir()
    (tmp_path / "run_20240102_130000").mkdir()
    (tmp_path / "run_20240103_140000").mkdir()

    runs = persistence.list_runs()

    assert len(runs) == 3
    assert runs[0] == "run_20240103_140000"  # Most recent first
    assert runs[-1] == "run_20240101_120000"
