package datastore

import (
	"context"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/app"
	"github.com/danielchalef/zep/pkg/memory"
)

var log = internal.GetLogger()

type DataStore[T any] interface {
	GetMemory(ctx context.Context,
		appState *app.AppState,
		sessionID string) (*memory.MemoryResponse, error)
	PostMemory(ctx context.Context,
		appState *app.AppState,
		sessionID string,
		memoryMessages memory.MemoryMessagesAndContext) error
	DeleteMemory(ctx context.Context, sessionID string) error
}

type BaseDataStore[T any] struct {
	client T
}
