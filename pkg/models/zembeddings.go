package models

import (
	"context"

	"github.com/getzep/zep/config"
)

type ZepEmbeddingsClient interface {
	// EmbedTexts embeds the given texts
	EmbedTexts(ctx context.Context, texts []string) ([][]float32, error)
	// Init initializes the Client
	Init(ctx context.Context, cfg *config.Config) error
}
