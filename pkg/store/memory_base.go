package store

import (
	"context"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

var log = internal.GetLogger()

// BaseMemoryStore is the base implementation of a MemoryStore. Client is the underlying datastore client, such as a
// database connection. The extractorObservers slice is used to store all registered Extractors.
type BaseMemoryStore[T any] struct {
	Client             T
	extractorObservers []models.Extractor
}

// Attach registers an Extractor to the MemoryStore
func (s *BaseMemoryStore[T]) Attach(observer models.Extractor) {
	s.extractorObservers = append(s.extractorObservers, observer)
}

// NotifyExtractors notifies all registered Extractors of a new MessageEvent
func (s *BaseMemoryStore[T]) NotifyExtractors(
	ctx context.Context,
	appState *models.AppState,
	eventData *models.MessageEvent,
) {
	for _, observer := range s.extractorObservers {
		go func(obs models.Extractor) {
			err := obs.Notify(ctx, appState, eventData)
			if err != nil {
				log.Errorf("BaseMemoryStore NotifyExtractors failed: %v", err)
			}
		}(observer)
	}
}
