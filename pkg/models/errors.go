package models

import (
	"errors"
	"fmt"
)

/* NotFoundError */

var ErrNotFound = errors.New("not found")

type NotFoundError struct {
	Resource string
}

func (e *NotFoundError) Error() string {
	return fmt.Sprintf("%s not found", e.Resource)
}

func (e *NotFoundError) Unwrap() error {
	return ErrNotFound
}

func NewNotFoundError(resource string) error {
	return &NotFoundError{Resource: resource}
}

/* BadRequestError */

var ErrBadRequest = errors.New("bad request")

type BadRequestError struct {
	Message string
}

func (e *BadRequestError) Error() string {
	return fmt.Sprintf("bad request: %s", e.Message)
}

func (e *BadRequestError) Unwrap() error {
	return ErrBadRequest
}

func NewBadRequestError(message string) error {
	return &BadRequestError{Message: message}
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

func (e AdvisoryLockError) Unwrap() error {
	return ErrLockAcquisitionFailed
}

func NewAdvisoryLockError(err error) error {
	return &AdvisoryLockError{Err: err}
}
