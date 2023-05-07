package models

import (
	"github.com/danielchalef/zep/config"
	"github.com/sashabaranov/go-openai"
)

// AppState is a struct that holds the state of the application
// Use cmd.NewAppState to create a new instance
type AppState struct {
	OpenAIClient *openai.Client
	MemoryStore  MemoryStore[any]
	Config       *config.Config
}
