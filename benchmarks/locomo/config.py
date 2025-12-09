"""Configuration management for LOCOMO evaluation harness."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class GraphParams(BaseModel):
    """Parameters for graph search."""

    edge_limit: int = Field(default=20, ge=1, le=100)
    edge_reranker: str = Field(default="cross_encoder")
    node_limit: int = Field(default=20, ge=1, le=100)
    node_reranker: str = Field(default="rrf")

    @field_validator("edge_reranker", "node_reranker")
    @classmethod
    def validate_reranker(cls, v: str) -> str:
        """Validate reranker type."""
        valid_rerankers = {"cross_encoder", "rrf", "mmr"}
        if v not in valid_rerankers:
            raise ValueError(f"Invalid reranker: {v}. Must be one of {valid_rerankers}")
        return v


class ModelConfig(BaseModel):
    """Model configuration."""

    response_model: str = Field(default="gpt-4o-mini")
    response_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    grader_model: str = Field(default="gpt-4o-mini")
    grader_temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class LocomoConfig(BaseModel):
    """LOCOMO-specific configuration."""

    num_users: int = Field(default=10, ge=1)
    max_session_count: int = Field(default=35, ge=1)
    data_url: str = Field(
        default="https://raw.githubusercontent.com/snap-research/locomo/refs/heads/main/data/locomo10.json"
    )


class BenchmarkConfig(BaseModel):
    """Top-level benchmark configuration."""

    evaluation_concurrency: int = Field(default=10, ge=1, le=50)
    ingestion_concurrency: int = Field(default=5, ge=1, le=20)
    warmup_enabled: bool = Field(default=True)
    warmup_concurrency: int = Field(default=10, ge=1, le=50)
    graph_params: GraphParams = Field(default_factory=GraphParams)
    models: ModelConfig = Field(default_factory=ModelConfig)
    locomo: LocomoConfig = Field(default_factory=LocomoConfig)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "BenchmarkConfig":
        """Load configuration from YAML file."""
        with open(config_path) as f:
            config_dict = yaml.safe_load(f)
        return cls(**config_dict)

    def to_yaml(self, output_path: str | Path) -> None:
        """Save configuration to YAML file."""
        with open(output_path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)


def load_config(config_path: str | None = None) -> BenchmarkConfig:
    """Load configuration from file or use defaults."""
    if config_path is None:
        config_path = "benchmark_config.yaml"

    if not os.path.exists(config_path):
        # Return default configuration
        return BenchmarkConfig()

    return BenchmarkConfig.from_yaml(config_path)
