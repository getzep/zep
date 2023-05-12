package extractors

import (
	"fmt"
	"sync"

	"github.com/getzep/zep/internal"
)

var log = internal.GetLogger()

type ExtractorError struct {
	message       string
	originalError error
}

func (e *ExtractorError) Error() string {
	return fmt.Sprintf("extractor error: %s (original error: %v)", e.message, e.originalError)
}

func NewExtractorError(message string, originalError error) *ExtractorError {
	return &ExtractorError{message: message, originalError: originalError}
}

// BaseExtractor is the base implementation of an Extractor
type BaseExtractor struct {
	sessionMutexes sync.Map
}

func (b *BaseExtractor) getSessionMutex(sessionID string) *sync.Mutex {
	mutexValue, _ := b.sessionMutexes.LoadOrStore(sessionID, &sync.Mutex{})
	return mutexValue.(*sync.Mutex)
}
