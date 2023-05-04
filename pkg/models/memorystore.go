package models

import (
	"context"
	"google.golang.org/appengine/log"
	"sync"
)

// MemoryStore is the interface for all MemoryStores
type MemoryStore[T any] interface {
	GetMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		lastNMessages int64,
		lastNTokens int64) (*MessageResponse, error)
	GetSummary(ctx context.Context,
		appState *AppState,
		sessionID string) (*Summary, error)
	PutMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		memoryMessages *MessagesAndSummary,
		wg *sync.WaitGroup) error
	PutSummary(ctx context.Context,
		appState *AppState,
		sessionID string,
		Summary *Summary) error
	SearchMemory(
		ctx context.Context,
		appState *AppState,
		sessionID string,
		query *SearchPayload) (*[]SearchResult, error)
	PruneSession(ctx context.Context,
		appState *AppState,
		sessionID string,
		messageCount int64,
		lockSession bool) error
	DeleteSession(ctx context.Context, sessionID string) error
	OnStart(ctx context.Context, appState *AppState) error
	Attach(observer Extractor)
	NotifyExtractors(
		ctx context.Context,
		appState *AppState,
		eventData *MessageEvent,
	)
}

// BaseMemoryStore is the base implementation of a MemoryStore
type BaseMemoryStore[T any] struct {
	Client             T
	extractorObservers []Extractor
}

// Attach registers an Extractor to the MemoryStore
func (s *BaseMemoryStore[T]) Attach(observer Extractor) {
	s.extractorObservers = append(s.extractorObservers, observer)
}

// NotifyExtractors notifies all registered Extractors of a new MessageEvent
func (s *BaseMemoryStore[T]) NotifyExtractors(
	ctx context.Context,
	appState *AppState,
	eventData *MessageEvent,
) {
	for _, observer := range s.extractorObservers {
		go func(obs Extractor) {
			err := obs.Notify(ctx, appState, eventData)
			if err != nil {
				log.Errorf(ctx, "BaseMemoryStore NotifyExtractors failed: %v", err)
			}
		}(observer)
	}
}
