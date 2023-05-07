package memorystore

import (
	"fmt"
	"github.com/danielchalef/zep/internal"
)

var log = internal.GetLogger()

type StorageError struct {
	message       string
	originalError error
}

func (e *StorageError) Error() string {
	return fmt.Sprintf("storage error: %s (original error: %v)", e.message, e.originalError)
}

func NewStorageError(message string, originalError error) *StorageError {
	return &StorageError{message: message, originalError: originalError}
}

func checkLastNParms(lastNTokens int, lastNMessages int) error {
	if lastNTokens > 0 {
		return NewStorageError("not implemented", nil)
	}

	if lastNMessages > 0 && lastNTokens > 0 {
		return NewStorageError("cannot specify both lastNMessages and lastNTokens", nil)
	}

	if lastNMessages < 0 || lastNTokens < 0 {
		return NewStorageError("cannot specify negative lastNMessages or lastNTokens", nil)
	}
	return nil
}
