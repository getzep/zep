package llms

import (
	"context"
	"fmt"

	"github.com/getzep/zep/pkg/models"
	"github.com/sashabaranov/go-openai"
)

func EmbedMessages(
	ctx context.Context,
	appState *models.AppState,
	text []string,
) ([]openai.Embedding, error) {
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

	resp, err := appState.OpenAIClient.CreateEmbeddings(ctx, req)
	if err != nil {
		return nil, NewLLMError("error while creating embedding", err)
	}

	return resp.Data, nil
}
