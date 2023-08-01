package models

import (
	"errors"
	"fmt"
)

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
