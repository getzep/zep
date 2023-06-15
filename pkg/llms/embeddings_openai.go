package llms

import (
	"context"

	"github.com/getzep/zep/pkg/models"
	"github.com/sashabaranov/go-openai"
)

func EmbedTextsOpenAI(
	ctx context.Context,
	appState *models.AppState,
	texts []string,
) ([][]float32, error) {
	embeddingModel := openai.AdaEmbeddingV2

	req := openai.EmbeddingRequest{
		Input: texts,
		Model: embeddingModel,
		User:  "zep_user",
	}

	resp, err := appState.OpenAIClient.CreateEmbeddings(ctx, req)
	if err != nil {
		return nil, NewLLMError("error while creating embedding", err)
	}

	m := make([][]float32, len(resp.Data))
	for i := range resp.Data {
		m[i] = resp.Data[i].Embedding
	}

	return m, nil
}
