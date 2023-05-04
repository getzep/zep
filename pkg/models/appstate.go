package models

import (
	"sync"

	"github.com/sashabaranov/go-openai"
)

// AppState is a struct that holds the state of the application
// Use cmd.NewAppState to create a new instance
type AppState struct {
	SessionLock      *sync.Map
	OpenAIClient     *openai.Client
	MemoryStore      MemoryStore[any]
	Embeddings       *Embeddings
	MaxSessionLength int64
}
