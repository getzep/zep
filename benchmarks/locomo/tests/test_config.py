"""Tests for configuration management."""

import pytest
from pydantic import ValidationError

from config import BenchmarkConfig, GraphParams, ModelConfig


def test_default_config():
    """Test default configuration."""
    config = BenchmarkConfig()
    assert config.evaluation_concurrency == 10
    assert config.graph_params.edge_limit == 20
    assert config.models.response_model == "gpt-6-luna"
    assert config.models.grader_model == "gpt-6-luna"
    assert config.models.generation_parameters() == {"reasoning_effort": "low"}
    assert config.models.generation_parameters(grader=True) == {"reasoning_effort": "low"}


def test_graph_params_validation():
    """Test graph parameter validation."""
    # Valid reranker
    params = GraphParams(edge_reranker="cross_encoder")
    assert params.edge_reranker == "cross_encoder"

    # Invalid reranker
    with pytest.raises(ValidationError):
        GraphParams(edge_reranker="invalid_reranker")

    # Limit validation
    with pytest.raises(ValidationError):
        GraphParams(edge_limit=0)

    with pytest.raises(ValidationError):
        GraphParams(edge_limit=101)


def test_model_config_validation():
    """Test model configuration validation."""
    # Valid temperature
    config = ModelConfig(response_temperature=0.5)
    assert config.response_temperature == 0.5

    # Invalid temperature
    with pytest.raises(ValidationError):
        ModelConfig(response_temperature=-0.1)

    with pytest.raises(ValidationError):
        ModelConfig(response_temperature=2.1)


def test_model_override_keeps_temperature():
    config = ModelConfig(
        response_model="custom-response",
        response_temperature=0.5,
        grader_model="custom-grader",
        grader_temperature=0.2,
    )
    assert config.generation_parameters() == {"temperature": 0.5}
    assert config.generation_parameters(grader=True) == {"temperature": 0.2}


def test_config_yaml_roundtrip(tmp_path):
    """Test saving and loading configuration from YAML."""
    config = BenchmarkConfig(
        evaluation_concurrency=20,
    )

    # Save to YAML
    yaml_path = tmp_path / "test_config.yaml"
    config.to_yaml(yaml_path)

    # Load from YAML
    loaded_config = BenchmarkConfig.from_yaml(yaml_path)

    assert loaded_config.evaluation_concurrency == config.evaluation_concurrency
