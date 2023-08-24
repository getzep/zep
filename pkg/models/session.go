package models

import (
	"context"
	"time"

	"github.com/google/uuid"
)

type Session struct {
	UUID      uuid.UUID              `json:"uuid"`
	CreatedAt time.Time              `json:"created_at"`
	UpdatedAt time.Time              `json:"updated_at"`
	DeletedAt *time.Time             `json:"deleted_at"`
	SessionID string                 `json:"session_id"`
	Metadata  map[string]interface{} `json:"metadata"`
	// Must be a pointer to allow for null values
	UserUUID *uuid.UUID `json:"user_uuid"`
}

type CreateSessionRequest struct {
	SessionID string `json:"session_id"`
	// Must be a pointer to allow for null values
	UserUUID *uuid.UUID             `json:"user_uuid"`
	Metadata map[string]interface{} `json:"metadata"`
}

type UpdateSessionRequest struct {
	SessionID string                 `json:"session_id"`
	Metadata  map[string]interface{} `json:"metadata"`
}

type SessionManager interface {
	Create(ctx context.Context, session *CreateSessionRequest) (*Session, error)
	Get(ctx context.Context, sessionID string) (*Session, error)
	Update(ctx context.Context, session *UpdateSessionRequest, isPrivileged bool) error
	Delete(ctx context.Context, sessionID string) error
	ListAll(ctx context.Context, cursor time.Time, limit int) ([]*Session, error)
}
