package llms

import (
	"context"
	"fmt"

	"github.com/getzep/zep/pkg/models"
)

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
		return embedTextsLocal(ctx, appState, text)
	}
}

func GetMessageEmbeddingModel(appState *models.AppState) (*models.EmbeddingModel, error) {
	model := appState.Config.Extractors.Embeddings.Model
	if model == "AdaEmbeddingV2" || model == "local" {
		return &models.EmbeddingModel{
			Name:       model,
			Dimensions: appState.Config.Extractors.Embeddings.Dimensions,
		}, nil
	}
	return nil, fmt.Errorf("unknown embedding model: %s", model)
}
