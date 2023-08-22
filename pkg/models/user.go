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

type UserStore interface {
	CreateUser(ctx context.Context, user *User) error
	GetUser(ctx context.Context, userID string) (*User, error)
	UpdateUser(ctx context.Context, user *User) error
	DeleteUser(ctx context.Context, userID string) error
}
