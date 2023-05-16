package llms

import (
	"context"
	"fmt"

	"github.com/avast/retry-go/v4"

	"github.com/getzep/zep/pkg/models"
	"github.com/sashabaranov/go-openai"
)

func EmbedMessages(
	ctx context.Context,
	appState *models.AppState,
	text []string,
) (*[]openai.Embedding, error) {
	if len(text) == 0 {
		return nil, NewLLMError("no text to embed", nil)
	}
	var embeddingModel openai.EmbeddingModel
	switch appState.Config.Extractors.Embeddings.Model {
	case "AdaEmbeddingV2":
		embeddingModel = openai.AdaEmbeddingV2
	default:
		return nil, NewLLMError(fmt.Sprintf("invalid embedding model: %s",
			appState.Config.LLM.Model), nil)
	}

	req := openai.EmbeddingRequest{
		Input: text,
		Model: embeddingModel,
		User:  "zep_user",
	}

	// Retry up to 3 times with exponential backoff, cancel after openAIAPITimeout
	retryCtx, cancel := context.WithTimeout(ctx, openAIAPITimeout)
	defer cancel()
	var resp openai.EmbeddingResponse
	err := retry.Do(
		func() error {
			var err error
			resp, err = appState.OpenAIClient.CreateEmbeddings(ctx, req)
			return err
		},
		retry.Attempts(3),
		retry.Context(retryCtx),
		retry.DelayType(retry.BackOffDelay),
		retry.OnRetry(func(n uint, err error) {
			log.Warningf("Retrying OpenAI API attempt #%d: %s\n", n, err)
		}),
	)

	if err != nil {
		return nil, NewLLMError("error while creating embedding", err)
	}

	return &resp.Data, nil
}
