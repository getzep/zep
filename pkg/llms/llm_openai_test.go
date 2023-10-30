package llms

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/stretchr/testify/assert"

	"github.com/getzep/zep/config"
)

func TestZepOpenAILLM_Init(t *testing.T) {
	cfg := &config.Config{
		LLM: config.LLM{
			Model:        "gpt-3.5-turbo",
			OpenAIAPIKey: "test-key",
		},
	}

	zllm := &ZepOpenAILLM{}

	err := zllm.Init(context.Background(), cfg)
	TestOpenAIClient_Init(t, err, zllm.llm, LLMClientType)
	assert.NotNil(t, zllm.tkm, "Expected tkm to be initialized")
}

func TestZepOpenAILLM_TestConfigureClient(t *testing.T) {
	zllm := &ZepOpenAILLM{}

	t.Run("Test with OpenAIAPIKey", func(t *testing.T) {
		cfg := &config.Config{
			LLM: config.LLM{
				OpenAIAPIKey: "test-key",
			},
		}

		options, err := zllm.configureClient(cfg)
		TestOpenAIClient_ConfigureClient(t, options, err, OpenAIAPIKeyTestCase)
	})

	t.Run("Test with AzureOpenAIEndpoint", func(t *testing.T) {
		cfg := &config.Config{
			LLM: config.LLM{
				OpenAIAPIKey:        "test-key",
				AzureOpenAIEndpoint: "https://azure.openai.com",
			},
		}

		options, err := zllm.configureClient(cfg)
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 5 {
			t.Errorf("Expected 4 options, got %d", len(options))
		}
	})

	t.Run("Test with AzureOpenAIEmbeddingModelAndCustomModelName", func(t *testing.T) {
		cfg := &config.Config{
			LLM: config.LLM{
				OpenAIAPIKey:        "test-key",
				AzureOpenAIEndpoint: "https://azure.openai.com",
				Model:               "some-model",
				AzureOpenAIModel: config.AzureOpenAIConfig{
					LLMDeployment:       "test-llm-deployment",
					EmbeddingDeployment: "test-embedding-deployment",
				},
			},
		}

		options, err := zllm.configureClient(cfg)
		TestOpenAIClient_ConfigureClient(t, options, err, AzureOpenAIEmbeddingModelTestCase)
	})

	t.Run("Test with OpenAIEndpointAndCustomModelName", func(t *testing.T) {
		cfg := &config.Config{
			LLM: config.LLM{
				OpenAIAPIKey:   "test-key",
				OpenAIEndpoint: "https://openai.com",
				Model:          "some-model",
			},
		}

		options, err := zllm.configureClient(cfg)
		TestOpenAIClient_ConfigureClient(t, options, err, OpenAIEndpointTestCase)
	})

	t.Run("Test with OpenAIOrgID", func(t *testing.T) {
		cfg := &config.Config{
			LLM: config.LLM{
				OpenAIAPIKey: "test-key",
				OpenAIOrgID:  "org-id",
			},
		}

		options, err := zllm.configureClient(cfg)
		TestOpenAIClient_ConfigureClient(t, options, err, OpenAIOrgIDTestCase)
	})
}

func TestZepOpenAILLM_Call(t *testing.T) {
	cfg := testutils.NewTestConfig()
	cfg.LLM.Model = "gpt-3.5-turbo"

	zllm, err := NewOpenAILLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	prompt := "Hello, world!"
	result, err := zllm.Call(context.Background(), prompt)
	assert.NoError(t, err, "Expected no error from Call")

	assert.NotEmpty(t, result, "Expected result to be non-empty")
}

func TestZepOpenAILLM_EmbedTexts(t *testing.T) {
	cfg := testutils.NewTestConfig()

	zllm, err := NewOpenAILLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	embeddings, err := zllm.EmbedTexts(context.Background(), EmbeddingsTestTexts)
	TestOpenAIClient_EmbedText(t, embeddings, err)
}

func TestZepOpenAILLM_GetTokenCount(t *testing.T) {
	cfg := testutils.NewTestConfig()

	zllm, err := NewOpenAILLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	tests := []struct {
		text     string
		expected int
	}{
		{"Hello, world!", 4},
		{"Another text", 2},
		// Add more test cases as needed
	}

	for _, tt := range tests {
		t.Run(tt.text, func(t *testing.T) {
			count, err := zllm.GetTokenCount(tt.text)
			assert.NoError(t, err, "Expected no error from GetTokenCount")
			assert.Equal(t, tt.expected, count, "Unexpected token count for '%s'", tt.text)
		})
	}
}
