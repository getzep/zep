package app

import (
	"fmt"
	"sync"

	openai "github.com/sashabaranov/go-openai"
)

type AppState struct {
	WindowSize     int64
	SessionCleanup *sync.Map
	OpenAIClient   *openai.Client
	LongTermMemory bool
}

type ZepError struct {
	RedisError                    error
	IncrementalSummarizationError string
}

func (e *ZepError) Error() string {
	if e.RedisError != nil {
		return fmt.Sprintf("Redis error: %v", e.RedisError)
	}

	return fmt.Sprintf("Incremental summarization error: %s", e.IncrementalSummarizationError)
}
