"""Tests for common data structures"""

from common import BenchmarkMetrics, EvaluationResult


class TestEvaluationResult:
    def test_create_result(self):
        result = EvaluationResult(
            user_id="test_user",
            question_id="q1",
            question="What is 2+2?",
            question_type="temporal-reasoning",
            hypothesis="4",
            gold_answer="4",
            context="test context",
            context_tokens=10,
            context_chars=100,
            duration=1.5,
            grade=True,
        )
        assert result.user_id == "test_user"
        assert result.grade is True
        assert result.evaluation_type == "zep"

    def test_model_dump(self):
        result = EvaluationResult(
            user_id="test_user",
            question_id="q1",
            question="What is 2+2?",
            question_type="temporal-reasoning",
            hypothesis="4",
            gold_answer="4",
            context="test context",
            context_tokens=10,
            context_chars=100,
            duration=1.5,
            grade=True,
        )
        d = result.model_dump()
        assert isinstance(d, dict)
        assert d["user_id"] == "test_user"
        assert d["grade"] is True
        assert d["context_tokens"] == 10


class TestBenchmarkMetrics:
    def test_create_metrics(self):
        metrics = BenchmarkMetrics(
            accuracy=0.75,
            correct_count=75,
            total_count=100,
            avg_response_duration=2.5,
            avg_retrieval_duration=0.5,
        )
        assert metrics.accuracy == 0.75
        assert metrics.correct_count == 75
        assert metrics.total_count == 100

    def test_model_dump(self):
        metrics = BenchmarkMetrics(
            accuracy=0.75,
            correct_count=75,
            total_count=100,
            avg_response_duration=2.5,
            avg_retrieval_duration=0.5,
        )
        d = metrics.model_dump()
        assert isinstance(d, dict)
        assert d["accuracy"] == 0.75
        assert d["correct_count"] == 75
        assert d["avg_response_duration"] == 2.5
