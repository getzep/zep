"""Tests for common data models."""

from common import EvaluationResult, Grade, LatencyStats, TokenStats


def test_evaluation_result():
    """Test EvaluationResult model."""
    result = EvaluationResult(
        user_id="user_test_123",
        test_id="test_001",
        category="navigation",
        difficulty="easy",
        query="test query",
        golden_answer="test answer",
        hypothesis="test hypothesis",
        context="test context",
        context_tokens=100,
        context_chars=500,
        retrieval_duration=0.5,
        response_duration=1.0,
        total_duration=1.5,
        grade=True,
    )

    assert result.user_id == "user_test_123"
    assert result.grade is True
    assert result.context_tokens == 100


def test_grade():
    """Test Grade model."""
    grade = Grade(is_correct="CORRECT", reasoning="The answer matches the gold standard.")

    assert grade.is_correct == "CORRECT"
    assert "matches" in grade.reasoning


def test_latency_stats():
    """Test LatencyStats model."""
    stats = LatencyStats(
        median=1.0,
        mean=1.2,
        std_dev=0.3,
        p50=1.0,
        p90=1.5,
        p95=1.7,
        p99=2.0,
        min=0.5,
        max=2.5,
    )

    assert stats.median == 1.0
    assert stats.p99 == 2.0


def test_token_stats():
    """Test TokenStats model."""
    stats = TokenStats(
        median=100.0,
        mean=110.0,
        p95=150.0,
        p99=180.0,
        min=50.0,
        max=200.0,
    )

    assert stats.median == 100.0
    assert stats.max == 200.0
