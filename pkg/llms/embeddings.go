package llms

import (
	"context"

	"github.com/getzep/zep/pkg/models"
)

func EmbedTexts(
	ctx context.Context,
	appState *models.AppState,
	model *models.EmbeddingModel,
	documentType string,
	text []string,
) ([][]float32, error) {
	if len(text) == 0 {
		return nil, NewLLMError("no text to embed", nil)
	}

	switch model.Service {
	case "openai":
		return EmbedTextsOpenAI(ctx, appState, text)
	case "local":
		return embedTextsLocal(ctx, appState, documentType, text)
	default:
		return nil, NewLLMError("invalid embedding service", nil)
	}
}

func GetMessageEmbeddingModel(
	appState *models.AppState,
	documentType string,
) (*models.EmbeddingModel, error) {
	switch documentType {
	case "message":
		config := appState.Config.Extractors.Messages.Embeddings
		return &models.EmbeddingModel{
			Service:    config.Service,
			Dimensions: config.Dimensions,
		}, nil
	case "document":
		config := appState.Config.Extractors.Documents.Embeddings
		return &models.EmbeddingModel{
			Service:    config.Service,
			Dimensions: config.Dimensions,
		}, nil
	default:
		return nil, NewLLMError("invalid document type", nil)

	}
}
