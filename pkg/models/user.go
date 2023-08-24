package models

import (
	"context"
	"time"

	"github.com/google/uuid"
)

type User struct {
	UUID      uuid.UUID              `json:"uuid"`
	CreatedAt time.Time              `json:"created_at"`
	UpdatedAt time.Time              `json:"updated_at"`
	DeletedAt *time.Time             `json:"deleted_at"`
	UserID    string                 `json:"user_id"`
	Metadata  map[string]interface{} `json:"metadata"`
}

type CreateUserRequest struct {
	UserID   string                 `json:"user_id"`
	Metadata map[string]interface{} `json:"metadata"`
}

type UpdateUserRequest struct {
	UUID     uuid.UUID              `json:"uuid"`
	UserID   string                 `json:"user_id"`
	Metadata map[string]interface{} `json:"metadata"`
}

type UserStore interface {
	Create(ctx context.Context, user *CreateUserRequest) (uuid.UUID, error)
	Get(ctx context.Context, userID string) (*User, error)
	Update(ctx context.Context, user *UpdateUserRequest) error
	Delete(ctx context.Context, userID string) error
	GetSessions(ctx context.Context, userID string) ([]*Session, error)
	ListAll(ctx context.Context, cursor time.Time, limit int) ([]*User, error)
}
