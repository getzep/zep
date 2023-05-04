package models

import "context"

type Memory[T any] interface {
	Get(ctx context.Context,
		appState *AppState,
		sessionID string) (*MessageResponse, error)
	Search(ctx context.Context,
		appState *AppState,
		sessionID string, query interface{}) (*MessageResponse, error)
}

type BaseMemory[T any] struct {
	DataStore *BaseMemoryStore[T]
}

type Message struct {
	Role    string         `json:"role"`
	Content string         `json:"content"`
	Meta    map[string]any `json:"meta,omitempty"`
}

type Summary struct {
	Content string         `json:"content"`
	Meta    map[string]any `json:"meta,omitempty"`
}

type MessagesAndSummary struct {
	Messages []Message      `json:"messages"`
	Summary  Summary        `json:"summary,omitempty"`
	Meta     map[string]any `json:"meta,omitempty"`
}

type MessageResponse struct {
	Messages []Message      `json:"messages"`
	Summary  Summary        `json:"summary,omitempty"`
	Tokens   int64          `json:"tokens"`
	Meta     map[string]any `json:"meta,omitempty"`
}

type MessageEvent struct {
	SessionID string         `json:"sessionId"`
	Messages  []Message      `json:"messages"`
	Meta      map[string]any `json:"meta,omitempty"`
}
