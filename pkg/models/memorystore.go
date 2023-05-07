package models

import (
	"context"
	"google.golang.org/appengine/log"
)

// MemoryStore interface
type MemoryStore[T any] interface {
	// GetMemory retrieves the Memory for a given sessionID. By default, a Memory includes both the most
	// recent Summary and the most recent Messages up to the last SummaryPoint or configured message window,
	// if a Summary has not yet been created.. This can be overridden by providing a lastNMessages value.
	GetMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		lastNMessages int) (*Memory, error)
	// GetSummary retrieves the most recent Summary for a given sessionID. The Summary return includes the UUID of the
	// SummaryPoint, which the most recent Message in the collection of messages that was used to generate the Summary.
	GetSummary(ctx context.Context,
		appState *AppState,
		sessionID string) (*Summary, error)
	// PutMemory stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
	PutMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		memoryMessages *Memory) error
	// PutSummary stores a new Summary for a given sessionID.
	PutSummary(ctx context.Context,
		appState *AppState,
		sessionID string,
		summary *Summary) error
	// PutMessageVectors stores a collection of Embeddings for a given sessionID. isEmbedded is a flag that
	// indicates whether the provided records have been embedded.
	PutMessageVectors(ctx context.Context,
		appState *AppState,
		sessionID string,
		embeddings []Embeddings,
		isEmbedded bool) error
	// GetMessageVectors retrieves a collection of Embeddings for a given sessionID. isEmbedded is a flag that
	// whether the Embeddings records have been embedded. The Embeddings extractor uses this internally to determine
	// which records still need to be embedded.
	GetMessageVectors(ctx context.Context,
		appState *AppState,
		sessionID string,
		isEmbedded bool) ([]Embeddings, error)
	// SearchMemory retrieves a collection of SearchResults for a given sessionID and query. Currently, the query
	// is a simple string, but this could be extended to support more complex queries in the future. The SearchResult
	// structure can include both Messages and Summaries. Currently, we only search Messages.
	SearchMemory(
		ctx context.Context,
		appState *AppState,
		sessionID string,
		query *SearchPayload,
		limit int) ([]SearchResult, error)
	// DeleteSession deletes all records for a given sessionID. This is a soft delete. Hard deletes will be handled
	// by a separate process or left to the implementation.
	DeleteSession(ctx context.Context, sessionID string) error
	// OnStart is called when the application starts. This is a good place to initialize any resources or configs that
	// are required by the MemoryStore implementation.
	OnStart(ctx context.Context, appState *AppState) error
	// Attach is used by Extractors to register themselves with the MemoryStore. This allows the MemoryStore to notify
	// the Extractors when new occur.
	Attach(observer Extractor)
	// NotifyExtractors notifies all registered Extractors of a new MessageEvent.
	NotifyExtractors(
		ctx context.Context,
		appState *AppState,
		eventData *MessageEvent,
	)
	// Close is called when the application is shutting down. This is a good place to clean up any resources used by
	// the MemoryStore implementation.
	Close() error
}

// BaseMemoryStore is the base implementation of a MemoryStore. Client is the underlying datastore client, such as a
// database connection. The extractorObservers slice is used to store all registered Extractors.
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
