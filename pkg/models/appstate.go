package models

import (
	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/llms/openairetryclient"
)

// AppState is a struct that holds the state of the application
// Use cmd.NewAppState to create a new instance
type AppState struct {
	OpenAIClient  *openairetryclient.OpenAIRetryClient
	MemoryStore   MemoryStore[any]
	DocumentStore DocumentStore[any]
	Config        *config.Config
}
