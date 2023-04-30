package main

import "context"

type DataStore[T any] interface {
	GetMemory(ctx context.Context,
		appState *AppState,
		sessionID string) (*MemoryResponse, error)
	PostMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		memoryMessages MemoryMessagesAndContext) error
	DeleteMemory(ctx context.Context, sessionID string) error
}

type BaseDataStore[T any] struct {
	client T
}
