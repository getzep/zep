package llms

import (
	"context"
	"errors"

	"github.com/getzep/zep/config"

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

	llmClient := appState.LLMClient

	if llmClient == nil {
		return nil, errors.New(InvalidLLMModelError)
	}

	if model.Service == "local" {
		return embedTextsLocal(ctx, appState, documentType, text)
	}

	embeddingsClient := appState.EmbeddingsClient

	if embeddingsClient != nil {
		return embeddingsClient.EmbedTexts(ctx, text)
	}

	return llmClient.EmbedTexts(ctx, text)
}

func GetEmbeddingModel(
	appState *models.AppState,
	documentType string,
) (*models.EmbeddingModel, error) {
	var cfg config.EmbeddingsConfig

	switch documentType {
	case "message":
		cfg = appState.Config.Extractors.Messages.Embeddings
	case "summary":
		cfg = appState.Config.Extractors.Messages.Summarizer.Embeddings
	case "document":
		cfg = appState.Config.Extractors.Documents.Embeddings
	default:
		return nil, errors.New("invalid document type")
	}

	return &models.EmbeddingModel{
		Service:    cfg.Service,
		Dimensions: cfg.Dimensions,
	}, nil
}
