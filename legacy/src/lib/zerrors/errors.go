package zerrors

import (
	"errors"
	"fmt"
)

type ErrorResponse struct {
	Message string `json:"message"`
}

/* NotFoundError */

var ErrNotFound = errors.New("not found")

type NotFoundError struct {
	Resource string
}

func (e *NotFoundError) Error() string {
	return fmt.Sprintf("%s not found", e.Resource)
}

func (*NotFoundError) Unwrap() error {
	return ErrNotFound
}

func NewNotFoundError(resource string) error {
	return &NotFoundError{Resource: resource}
}

/* UnauthorizedError */

var ErrUnauthorized = errors.New("unauthorized")

type UnauthorizedError struct {
	Message string
}

func (e *UnauthorizedError) Error() string {
	return fmt.Sprintf("unauthorized %s", e.Message)
}

func (*UnauthorizedError) Unwrap() error {
	return ErrUnauthorized
}

func NewUnauthorizedError(message string) error {
	return &UnauthorizedError{Message: message}
}

/* BadRequestError */

var ErrBadRequest = errors.New("bad request")

type BadRequestError struct {
	Message string
}

func (e *BadRequestError) Error() string {
	return fmt.Sprintf("bad request: %s", e.Message)
}

func (*BadRequestError) Unwrap() error {
	return ErrBadRequest
}

func NewBadRequestError(message string) error {
	return &BadRequestError{Message: message}
}

/* CustomMessageInternalError */

var ErrInternalCustomMessage = errors.New("internal error")

type CustomMessageInternalError struct {
	// User friendly message
	ExternalMessage string
	// Internal message, raw error message to be logged to sentry
	InternalMessage string
}

func (e *CustomMessageInternalError) Error() string {
	return e.ExternalMessage
}

func (*CustomMessageInternalError) Unwrap() error {
	return ErrInternalCustomMessage
}

func NewCustomMessageInternalError(externalMessage, internalMessage string) error {
	return &CustomMessageInternalError{ExternalMessage: externalMessage, InternalMessage: internalMessage}
}

var ErrDeprecated = errors.New("deprecated")

type DeprecationError struct {
	Message string
}

func (e *DeprecationError) Error() string {
	return fmt.Sprintf("deprecation error: %s", e.Message)
}

func (*DeprecationError) Unwrap() error {
	return ErrDeprecated
}

func NewDeprecationError(message string) error {
	return &DeprecationError{Message: message}
}

var ErrLockAcquisitionFailed = errors.New("failed to acquire advisory lock")

type AdvisoryLockError struct {
	Err error
}

func (e AdvisoryLockError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("failed to acquire advisory lock: %v", e.Err)
	}
	return ErrLockAcquisitionFailed.Error()
}

func (AdvisoryLockError) Unwrap() error {
	return ErrLockAcquisitionFailed
}

func NewAdvisoryLockError(err error) error {
	return &AdvisoryLockError{Err: err}
}

var ErrSessionEnded = errors.New("session ended")

type SessionEndedError struct {
	Message string
}

func (e *SessionEndedError) Error() string {
	return fmt.Sprintf("session ended: %s", e.Message)
}

func (*SessionEndedError) Unwrap() error {
	return ErrSessionEnded
}

func NewSessionEndedError(message string) error {
	return &SessionEndedError{Message: message}
}

var ErrRepeatedPattern = errors.New("llm provider reports too many repeated characters")

type RepeatedPatternError struct {
	Message string
}

func (e *RepeatedPatternError) Error() string {
	return fmt.Sprintf("repeated pattern: %s", e.Message)
}

func (*RepeatedPatternError) Unwrap() error {
	return ErrRepeatedPattern
}

func NewRepeatedPatternError(message string) error {
	return &RepeatedPatternError{Message: message}
}
