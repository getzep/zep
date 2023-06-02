package llms

import (
	"context"

	"github.com/getzep/zep/pkg/models"
)

var ShortTextEmbeddingModel = models.EmbeddingModel{
	Name:         "multi-qa-MiniLM-L6-cos-v1",
	Dimensions:   384,
	IsNormalized: true,
}

var OpenAIEmbeddingModel = models.EmbeddingModel{
	Name:         "AdaEmbeddingV2",
	Dimensions:   1536,
	IsNormalized: true,
}

func EmbedTexts(
	ctx context.Context,
	appState *models.AppState,
	model *models.EmbeddingModel,
	text []string,
) ([][]float32, error) {
	if len(text) == 0 {
		return nil, NewLLMError("no text to embed", nil)
	}

	switch model.Name {
	case "AdaEmbeddingV2":
		return EmbedTextsOpenAI(ctx, appState, text)
	default:
		return embedTextsLocal(ctx, appState, model, text)
	}
}

func GetMessageEmbeddingModel(appState *models.AppState) *models.EmbeddingModel {
	switch appState.Config.Extractors.Embeddings.Messages.Provider {
	case "openai":
		return &OpenAIEmbeddingModel
	default:
		return &ShortTextEmbeddingModel
	}
}
