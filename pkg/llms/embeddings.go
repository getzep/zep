package llms

import (
	"context"
	"errors"

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
		return nil, errors.New("no text to embed")
	}

	if appState.LLMClient == nil {
		return nil, errors.New(InvalidLLMModelError)
	}

	if model.Service == "local" {
		return embedTextsLocal(ctx, appState, documentType, text)
	}
	return appState.LLMClient.EmbedTexts(ctx, text)
}

func GetEmbeddingModel(
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
		return nil, errors.New("invalid document type")

	}
}
