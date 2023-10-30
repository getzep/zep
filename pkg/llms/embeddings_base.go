package llms

import (
	"context"
	"fmt"

	"github.com/getzep/zep/pkg/models"

	"github.com/getzep/zep/config"
)

const InvalidEmbeddingsClientError = "embeddings client is not set or is invalid"

type EmbeddingsClientError struct {
	message       string
	originalError error
}

func (e *EmbeddingsClientError) Error() string {
	return fmt.Sprintf("embeddings client error: %s (original error: %v)", e.message, e.originalError)
}

func NewEmbeddingsClientError(message string, originalError error) *EmbeddingsClientError {
	return &EmbeddingsClientError{message: message, originalError: originalError}
}

func NewEmbeddingsClient(ctx context.Context, cfg *config.Config) (models.ZepEmbeddingsClient, error) {
	switch cfg.EmbeddingsClient.Service {
	// For now we only support OpenAI embeddings
	case "openai":
		// EmbeddingsDeployment is required if using external embeddings with AzureOpenAI
		if cfg.EmbeddingsClient.AzureOpenAIEndpoint != "" && cfg.EmbeddingsClient.AzureOpenAIModel.EmbeddingDeployment == "" {
			err := InvalidEmbeddingsDeploymentError(cfg.EmbeddingsClient.Service)
			return nil, err
		}
		// The logic is the same if custom OpenAI Endpoint is set or not
		// since the model name will be set automatically in this case
		return NewOpenAIEmbeddingsClient(ctx, cfg)
	default:
		return nil, fmt.Errorf("invalid embeddings service: %s", cfg.EmbeddingsClient.Service)
	}
}
