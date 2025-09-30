#!/usr/bin/env python3
"""
Configuration management for LongMemEval benchmark
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class GraphParams(BaseModel):
    """Parameters for Zep graph retrieval"""

    edge_limit: int = Field(default=20, ge=0, le=100)
    node_limit: int = Field(default=5, ge=0, le=50)
    episode_limit: int = Field(default=5, ge=0, le=50)
    edge_reranker: str = Field(default="cross_encoder")
    node_reranker: str = Field(default="cross_encoder")
    episode_reranker: str = Field(default="cross_encoder")

    @field_validator("edge_reranker", "node_reranker", "episode_reranker")
    @classmethod
    def validate_reranker(cls, v: str) -> str:
        valid_rerankers = {"cross_encoder", "rrf", "none"}
        if v not in valid_rerankers:
            raise ValueError(f"Reranker must be one of {valid_rerankers}, got: {v}")
        return v


class ModelConfig(BaseModel):
    """LLM model configuration"""

    response_model: str = Field(default="gpt-4o")
    grader_model: str = Field(default="gpt-4o")

    # GPT-5 / reasoning model specific parameters
    reasoning_effort: str | None = Field(
        default=None,
        description="For reasoning models (gpt-5, o1, o3): minimal, low, medium, or high"
    )
    max_completion_tokens: int | None = Field(
        default=None,
        ge=1,
        description="For reasoning models: max tokens in completion"
    )

    # Traditional model parameters (not used for reasoning models)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, v: str | None) -> str | None:
        if v is not None:
            valid_efforts = {"minimal", "low", "medium", "high"}
            if v not in valid_efforts:
                raise ValueError(
                    f"reasoning_effort must be one of {valid_efforts}, got: {v}"
                )
        return v

    def is_reasoning_model(self) -> bool:
        """Check if the response model is a reasoning model (GPT-5, o1, o3)"""
        model_lower = self.response_model.lower()
        return any(prefix in model_lower for prefix in ["gpt-5", "o1", "o3"])

    def is_grader_reasoning_model(self) -> bool:
        """Check if the grader model is a reasoning model (GPT-5, o1, o3)"""
        model_lower = self.grader_model.lower()
        return any(prefix in model_lower for prefix in ["gpt-5", "o1", "o3"])


class BenchmarkConfig(BaseModel):
    """Complete benchmark configuration"""

    graph_params: GraphParams
    models: ModelConfig

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BenchmarkConfig":
        """Load configuration from YAML file"""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path) as f:
            data = yaml.safe_load(f)

        # Extract first element from arrays (legacy format support)
        graph_params_data = {}
        for key, value in data.get("graph_params", {}).items():
            graph_params_data[key] = value[0] if isinstance(value, list) else value

        models_data = {}
        for key, value in data.get("models", {}).items():
            models_data[key] = value[0] if isinstance(value, list) else value

        return cls(
            graph_params=GraphParams(**graph_params_data),
            models=ModelConfig(**models_data),
        )
