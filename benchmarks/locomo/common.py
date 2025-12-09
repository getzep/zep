"""Common data models for LOCOMO evaluation harness."""

from pydantic import BaseModel, Field


class CompletenessGrade(BaseModel):
    """Context completeness evaluation result."""

    completeness: str = Field(description="COMPLETE, PARTIAL, or INSUFFICIENT")
    reasoning: str = Field(description="Explain why the context is sufficient or what is missing.")
    missing_elements: list[str] = Field(
        default_factory=list, description="List of missing information elements"
    )
    present_elements: list[str] = Field(
        default_factory=list,
        description="List of information elements found in context",
    )


class EvaluationResult(BaseModel):
    """Result of evaluating a single test case."""

    user_id: str | None = None  # For user-based graphs (v1)
    graph_id: str | None = None  # For default graphs (v2)
    test_id: str
    category: str
    difficulty: str
    query: str
    golden_answer: str
    hypothesis: str
    context: str
    context_tokens: int
    context_chars: int
    retrieval_duration: float
    response_duration: float
    total_duration: float
    grade: bool
    grade_reasoning: str | None = None
    completeness_grade: str | None = None  # COMPLETE, PARTIAL, or INSUFFICIENT
    completeness_reasoning: str | None = None
    missing_elements: list[str] = Field(default_factory=list)
    present_elements: list[str] = Field(default_factory=list)


class LatencyStats(BaseModel):
    """Statistical distribution of latencies."""

    median: float
    mean: float
    std_dev: float
    p50: float
    p90: float
    p95: float
    p99: float
    min: float
    max: float


class TokenStats(BaseModel):
    """Statistical distribution of token counts."""

    median: float
    mean: float
    p95: float
    p99: float
    min: float
    max: float


class CategoryMetrics(BaseModel):
    """Metrics grouped by category."""

    category: str
    accuracy: float
    correct_count: int
    total_count: int
    avg_retrieval_duration: float
    avg_response_duration: float


class DifficultyMetrics(BaseModel):
    """Metrics grouped by difficulty level."""

    difficulty: str
    accuracy: float
    correct_count: int
    total_count: int


class BenchmarkMetrics(BaseModel):
    """Aggregate metrics for a benchmark run."""

    accuracy: float
    correct_count: int
    total_count: int

    # Context completeness metrics
    completeness_complete_rate: float = 0.0
    completeness_complete_count: int = 0
    completeness_partial_rate: float = 0.0
    completeness_partial_count: int = 0
    completeness_insufficient_rate: float = 0.0
    completeness_insufficient_count: int = 0
    accuracy_with_complete_context: float | None = None
    correct_with_complete_context: int = 0
    total_with_complete_context: int = 0

    # Latency distributions
    retrieval_duration_stats: LatencyStats
    response_duration_stats: LatencyStats
    total_duration_stats: LatencyStats

    # Context analysis
    context_token_stats: TokenStats
    context_char_stats: TokenStats

    # Breakdown by category
    by_category: list[CategoryMetrics]

    # Breakdown by difficulty
    by_difficulty: list[DifficultyMetrics]


class Grade(BaseModel):
    """LLM grading response."""

    is_correct: str = Field(description="CORRECT or WRONG")
    reasoning: str = Field(description="Explain why the answer is correct or incorrect.")
