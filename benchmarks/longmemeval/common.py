#!/usr/bin/env python3
"""
Common models and data structures for LongMemEval benchmark
"""

from pydantic import BaseModel, Field
from zep_cloud import EntityEdge, EntityNode, Episode


class Grade(BaseModel):
    """LLM grading result"""

    is_correct: str = Field(description="yes or no")


class ContextData(BaseModel):
    """Retrieved context from Zep graph"""

    edges: list[EntityEdge]
    nodes: list[EntityNode]
    episodes: list[Episode]


class EvaluationResult(BaseModel):
    """Result of evaluating a single conversation"""

    user_id: str
    question_id: str
    question: str
    question_type: str
    hypothesis: str
    gold_answer: str
    context: str
    context_tokens: int
    context_chars: int
    duration: float
    grade: bool
    evaluation_type: str = "zep"


class BenchmarkMetrics(BaseModel):
    """Aggregate metrics from benchmark run"""

    accuracy: float
    correct_count: int
    total_count: int
    avg_response_duration: float
    avg_retrieval_duration: float
