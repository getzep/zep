"""Tests for configuration validation"""

import pytest
from pydantic import ValidationError

from config import BenchmarkConfig, GraphParams, ModelConfig


class TestGraphParams:
    def test_valid_params(self):
        params = GraphParams(
            edge_limit=20,
            node_limit=5,
            episode_limit=5,
            edge_reranker="cross_encoder",
            node_reranker="cross_encoder",
            episode_reranker="cross_encoder",
        )
        assert params.edge_limit == 20
        assert params.edge_reranker == "cross_encoder"

    def test_default_values(self):
        params = GraphParams()
        assert params.edge_limit == 20
        assert params.node_limit == 5
        assert params.episode_limit == 5

    def test_invalid_reranker(self):
        with pytest.raises(ValidationError):
            GraphParams(edge_reranker="invalid_reranker")

    def test_negative_limits(self):
        with pytest.raises(ValidationError):
            GraphParams(edge_limit=-1)

    def test_excessive_limits(self):
        with pytest.raises(ValidationError):
            GraphParams(edge_limit=1000)


class TestModelConfig:
    def test_valid_config(self):
        config = ModelConfig(
            response_model="gpt-4o",
            grader_model="gpt-4o",
        )
        assert config.response_model == "gpt-4o"
        assert config.grader_model == "gpt-4o"

    def test_default_values(self):
        config = ModelConfig()
        assert config.response_model == "gpt-4o"
        assert config.grader_model == "gpt-4o"
        assert config.temperature == 0.0
        assert config.reasoning_effort is None

    def test_reasoning_model_detection(self):
        """Test detection of reasoning models"""
        gpt5_config = ModelConfig(response_model="gpt-5")
        assert gpt5_config.is_reasoning_model() is True

        o1_config = ModelConfig(response_model="o1-preview")
        assert o1_config.is_reasoning_model() is True

        o3_config = ModelConfig(response_model="o3-mini")
        assert o3_config.is_reasoning_model() is True

        gpt4_config = ModelConfig(response_model="gpt-4o")
        assert gpt4_config.is_reasoning_model() is False

    def test_reasoning_effort_validation(self):
        """Test reasoning_effort parameter validation"""
        valid_config = ModelConfig(reasoning_effort="medium")
        assert valid_config.reasoning_effort == "medium"

        with pytest.raises(ValidationError):
            ModelConfig(reasoning_effort="invalid")

    def test_gpt5_config(self):
        """Test full GPT-5 configuration"""
        config = ModelConfig(
            response_model="gpt-5",
            grader_model="gpt-4o",
            reasoning_effort="high",
            max_completion_tokens=10000,
        )
        assert config.response_model == "gpt-5"
        assert config.reasoning_effort == "high"
        assert config.max_completion_tokens == 10000
        assert config.is_reasoning_model() is True
        assert config.is_grader_reasoning_model() is False

    def test_traditional_model_config(self):
        """Test traditional model configuration"""
        config = ModelConfig(
            response_model="gpt-4.1",
            temperature=0.5,
            max_tokens=2000,
        )
        assert config.temperature == 0.5
        assert config.max_tokens == 2000
        assert config.is_reasoning_model() is False


class TestBenchmarkConfig:
    def test_from_yaml_legacy_format(self, tmp_path):
        """Test loading legacy config format with arrays"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
graph_params:
  edge_limit: [20]
  node_limit: [5]
  episode_limit: [5]
  edge_reranker: [cross_encoder]
  node_reranker: [cross_encoder]
  episode_reranker: [cross_encoder]
models:
  response_model: [gpt-4o]
  grader_model: [gpt-4o]
""")

        config = BenchmarkConfig.from_yaml(config_file)
        assert config.graph_params.edge_limit == 20
        assert config.models.response_model == "gpt-4o"

    def test_from_yaml_new_format(self, tmp_path):
        """Test loading new config format without arrays"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
graph_params:
  edge_limit: 20
  node_limit: 5
  episode_limit: 5
  edge_reranker: cross_encoder
  node_reranker: cross_encoder
  episode_reranker: cross_encoder
models:
  response_model: gpt-4o
  grader_model: gpt-4o
""")

        config = BenchmarkConfig.from_yaml(config_file)
        assert config.graph_params.edge_limit == 20
        assert config.models.response_model == "gpt-4o"

    def test_from_yaml_missing_file(self):
        with pytest.raises(FileNotFoundError):
            BenchmarkConfig.from_yaml("nonexistent.yaml")

    def test_from_yaml_with_gpt5(self, tmp_path):
        """Test loading GPT-5 config with reasoning parameters"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
graph_params:
  edge_limit: [20]
  node_limit: [5]
  episode_limit: [5]
  edge_reranker: [cross_encoder]
  node_reranker: [cross_encoder]
  episode_reranker: [cross_encoder]
models:
  response_model: [gpt-5]
  grader_model: [gpt-4o]
  reasoning_effort: [high]
  max_completion_tokens: [10000]
  temperature: [0.0]
""")

        config = BenchmarkConfig.from_yaml(config_file)
        assert config.models.response_model == "gpt-5"
        assert config.models.reasoning_effort == "high"
        assert config.models.max_completion_tokens == 10000
        assert config.models.is_reasoning_model() is True

    def test_concurrency_default(self):
        """Test default concurrency value"""
        config = BenchmarkConfig(
            graph_params=GraphParams(),
            models=ModelConfig(),
        )
        assert config.concurrency == 2

    def test_concurrency_custom(self, tmp_path):
        """Test custom concurrency value from YAML"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
concurrency: 5
graph_params:
  edge_limit: [20]
models:
  response_model: [gpt-4o]
""")

        config = BenchmarkConfig.from_yaml(config_file)
        assert config.concurrency == 5

    def test_concurrency_validation(self):
        """Test concurrency bounds validation"""
        with pytest.raises(ValidationError):
            BenchmarkConfig(
                graph_params=GraphParams(),
                models=ModelConfig(),
                concurrency=0,  # Below minimum
            )

        with pytest.raises(ValidationError):
            BenchmarkConfig(
                graph_params=GraphParams(),
                models=ModelConfig(),
                concurrency=11,  # Above maximum
            )
