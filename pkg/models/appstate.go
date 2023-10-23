package models

import (
	"github.com/getzep/zep/config"
)

// AppState is a struct that holds the state of the application
// Use cmd.NewAppState to create a new instance
type AppState struct {
	LLMClient     ZepLLM
	MemoryStore   MemoryStore[any]
	DocumentStore DocumentStore[any]
	UserStore     UserStore
	TaskRouter    TaskRouter
	TaskPublisher TaskPublisher
	Config        *config.Config
}
