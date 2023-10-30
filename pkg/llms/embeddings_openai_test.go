package llms

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/stretchr/testify/assert"

	"github.com/getzep/zep/config"
)

func TestZepOpenAIEmbeddings_Init(t *testing.T) {
	cfg := &config.Config{
		EmbeddingsClient: config.EmbeddingsClient{
			OpenAIAPIKey: "test-key",
		},
	}

	zembeddings := &ZepOpenAIEmbeddingsClient{}

	err := zembeddings.Init(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from Init")
	assert.NotNil(t, zembeddings.client, "Expected client to be initialized")
}

func TestZepOpenAIEmbeddings_TestConfigureClient(t *testing.T) {
	zembeddings := &ZepOpenAIEmbeddingsClient{}

	t.Run("Test with OpenAIAPIKey", func(t *testing.T) {
		cfg := &config.Config{
			EmbeddingsClient: config.EmbeddingsClient{
				OpenAIAPIKey: "test-key",
			},
		}

		options, err := zembeddings.configureClient(cfg)
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 3 {
			t.Errorf("Expected 2 options, got %d", len(options))
		}
	})

	t.Run("Test with AzureOpenAIEmbeddingModel", func(t *testing.T) {
		cfg := &config.Config{
			EmbeddingsClient: config.EmbeddingsClient{
				OpenAIAPIKey:        "test-key",
				AzureOpenAIEndpoint: "https://azure.openai.com",
				AzureOpenAIModel: config.AzureOpenAIConfig{
					EmbeddingDeployment: "test-embedding-deployment",
				},
			},
		}

		options, err := zembeddings.configureClient(cfg)
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 6 {
			t.Errorf("Expected 6 options, got %d", len(options))
		}
	})

	t.Run("Test with OpenAIEndpoint", func(t *testing.T) {
		cfg := &config.Config{
			EmbeddingsClient: config.EmbeddingsClient{
				OpenAIAPIKey:   "test-key",
				OpenAIEndpoint: "https://openai.com",
			},
		}

		options, err := zembeddings.configureClient(cfg)
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 4 {
			t.Errorf("Expected 3 options, got %d", len(options))
		}
	})

	t.Run("Test with OpenAIOrgID", func(t *testing.T) {
		cfg := &config.Config{
			EmbeddingsClient: config.EmbeddingsClient{
				OpenAIAPIKey: "test-key",
				OpenAIOrgID:  "org-id",
			},
		}

		options, err := zembeddings.configureClient(cfg)
		if err != nil {
			t.Errorf("Unexpected error: %v", err)
		}

		if len(options) != 4 {
			t.Errorf("Expected 3 options, got %d", len(options))
		}
	})
}

func TestZepOpenAIEmbeddings_EmbedTexts(t *testing.T) {
	cfg := testutils.NewTestConfig()

	zembeddings, err := NewOpenAIEmbeddingsClient(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAIEmbeddingsClient")

	texts := []string{"Hello, world!", "Another text"}
	embeddings, err := zembeddings.EmbedTexts(context.Background(), texts)
	assert.NoError(t, err, "Expected no error from EmbedTexts")
	assert.Equal(t, len(texts), len(embeddings), "Expected embeddings to have same length as texts")
	assert.NotZero(t, embeddings[0], "Expected embeddings to be non-zero")
	assert.NotZero(t, embeddings[1], "Expected embeddings to be non-zero")
}
