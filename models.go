package main

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

type SearchPayload struct {
	Text string `json:"text"`
}

type MemoryMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type MemoryMessagesAndContext struct {
	Messages []MemoryMessage `json:"messages"`
	Summary  string          `json:"summary,omitempty"`
}

type MemoryResponse struct {
	Messages []MemoryMessage `json:"messages"`
	Summary  string          `json:"summary,omitempty"`
	Tokens   int64           `json:"tokens"`
}

type HealthCheckResponse struct {
	Now int64 `json:"now"`
}

type AckResponse struct {
	Status string `json:"status"`
}

type RedisearchResult struct {
	Role    string  `json:"role"`
	Content string  `json:"content"`
	Dist    float64 `json:"dist"`
}

type PapyrusError struct {
	RedisError                    error
	IncrementalSummarizationError string
}

func (e *PapyrusError) Error() string {
	if e.RedisError != nil {
		return fmt.Sprintf("Redis error: %v", e.RedisError)
	}

	return fmt.Sprintf("Incremental summarization error: %s", e.IncrementalSummarizationError)
}
