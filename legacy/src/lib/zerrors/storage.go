package zerrors

import (
	"errors"
	"fmt"

	"github.com/uptrace/bun/driver/pgdriver"
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

func (*EmbeddingMismatchError) Unwrap() error {
	return ErrEmbeddingMismatch
}

func NewEmbeddingMismatchError(
	originalError error,
) *EmbeddingMismatchError {
	return &EmbeddingMismatchError{
		OriginalError: originalError,
	}
}

func CheckForIntegrityViolationError(err error, integrityErrorMessage, generalErrorMessage string) error {
	var pgDriverError pgdriver.Error
	if errors.As(err, &pgDriverError) && pgDriverError.IntegrityViolation() {
		return NewBadRequestError(integrityErrorMessage)
	}
	return fmt.Errorf("%s %w", generalErrorMessage, err)
}
