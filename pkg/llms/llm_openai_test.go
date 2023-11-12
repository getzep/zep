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

	zllm, err := NewOpenAILLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	err = zllm.Init(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from Init")

	z, ok := zllm.(*ZepLLM)
	assert.True(t, ok, "Expected ZepLLM")

	assert.NotNil(t, z.llm, "Expected client to be initialized")

	o, ok := z.llm.(*ZepOpenAILLM)
	assert.True(t, ok, "Expected ZepOpenAILLM")
	assert.NotNil(t, o.client, "Expected tkm to be initialized")
	assert.NotNil(t, o.tkm, "Expected tkm to be initialized")
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
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 3 {
			t.Errorf("Expected 2 options, got %d", len(options))
		}
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
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 6 {
			t.Errorf("Expected 6 options, got %d", len(options))
		}
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
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 4 {
			t.Errorf("Expected 3 options, got %d", len(options))
		}
	})

	t.Run("Test with OpenAIOrgID", func(t *testing.T) {
		cfg := &config.Config{
			LLM: config.LLM{
				OpenAIAPIKey: "test-key",
				OpenAIOrgID:  "org-id",
			},
		}

		options, err := zllm.configureClient(cfg)
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 4 {
			t.Errorf("Expected 3 options, got %d", len(options))
		}
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

	texts := []string{"Hello, world!", "Another text"}
	embeddings, err := zllm.EmbedTexts(context.Background(), texts)
	assert.NoError(t, err, "Expected no error from EmbedTexts")
	assert.Equal(t, len(texts), len(embeddings), "Expected embeddings to have same length as texts")
	assert.NotZero(t, embeddings[0], "Expected embeddings to be non-zero")
	assert.NotZero(t, embeddings[1], "Expected embeddings to be non-zero")
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
