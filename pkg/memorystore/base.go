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
