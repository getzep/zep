package models

import (
	"context"

	"github.com/getzep/zep/config"

	"github.com/tmc/langchaingo/llms"
)

type ZepLLM interface {
	// Call runs the LLM chat completion against the prompt
	// this version of Call uses the chat endpoint of an LLM, but
	// we pass in a simple string prompt
	Call(
		ctx context.Context,
		prompt string,
		options ...llms.CallOption,
	) (string, error)
	// EmbedTexts embeds the given texts
	EmbedTexts(ctx context.Context, texts []string) ([][]float32, error)
	// GetTokenCount returns the number of tokens in the given text
	GetTokenCount(text string) (int, error)
	// Init initializes the LLM
	Init(ctx context.Context, cfg *config.Config) error
}
