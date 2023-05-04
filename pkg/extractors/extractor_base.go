package extractors

import (
	"fmt"

	"github.com/danielchalef/zep/internal"
)

var log = internal.GetLogger()

// Custom error type
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
