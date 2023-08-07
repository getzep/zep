package store

import (
	"errors"
	"fmt"
)

type StorageError struct {
	Message       string
	OriginalError error
}

func (e *StorageError) Error() string {
	return fmt.Sprintf("storage error: %s (original error: %v)", e.Message, e.OriginalError)
}

func NewStorageError(message string, originalError error) *StorageError {
	return &StorageError{Message: message, OriginalError: originalError}
}

var ErrEmbeddingMismatch = errors.New("embedding width mismatch")

type EmbeddingMismatchError struct {
	Message       string
	OriginalError error
}

func (e *EmbeddingMismatchError) Error() string {
	return fmt.Sprintf(
		"embedding width mismatch. please ensure that the embeddings "+
			"you have configured in the zep config are the same width as those "+
			"you are generating. (original error: %v)",
		e.OriginalError,
	)
}

func (e *EmbeddingMismatchError) Unwrap() error {
	return ErrEmbeddingMismatch
}

func NewEmbeddingMismatchError(
	originalError error,
) *EmbeddingMismatchError {
	return &EmbeddingMismatchError{
		OriginalError: originalError,
	}
}
