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
	TestOpenAIClient_Init(t, err, zembeddings.client, EmbeddingsClientType)
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
		TestOpenAIClient_ConfigureClient(t, options, err, OpenAIAPIKeyTestCase)
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
		TestOpenAIClient_ConfigureClient(t, options, err, AzureOpenAIEmbeddingModelTestCase)
	})

	t.Run("Test with OpenAIEndpoint", func(t *testing.T) {
		cfg := &config.Config{
			EmbeddingsClient: config.EmbeddingsClient{
				OpenAIAPIKey:   "test-key",
				OpenAIEndpoint: "https://openai.com",
			},
		}

		options, err := zembeddings.configureClient(cfg)
		TestOpenAIClient_ConfigureClient(t, options, err, OpenAIEndpointTestCase)
	})

	t.Run("Test with OpenAIOrgID", func(t *testing.T) {
		cfg := &config.Config{
			EmbeddingsClient: config.EmbeddingsClient{
				OpenAIAPIKey: "test-key",
				OpenAIOrgID:  "org-id",
			},
		}

		options, err := zembeddings.configureClient(cfg)
		TestOpenAIClient_ConfigureClient(t, options, err, OpenAIOrgIDTestCase)
	})
}

func TestZepOpenAIEmbeddings_EmbedTexts(t *testing.T) {
	cfg := testutils.NewTestConfig()

	zembeddings, err := NewOpenAIEmbeddingsClient(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAIEmbeddingsClient")

	embeddings, err := zembeddings.EmbedTexts(context.Background(), EmbeddingsTestTexts)
	TestOpenAIClient_EmbedText(t, embeddings, err)
}
