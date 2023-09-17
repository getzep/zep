package models

import (
	"context"
	"time"

	"github.com/google/uuid"
)

type Session struct {
	UUID      uuid.UUID              `json:"uuid"`
	ID        int64                  `json:"id"`
	CreatedAt time.Time              `json:"created_at"`
	UpdatedAt time.Time              `json:"updated_at"`
	DeletedAt *time.Time             `json:"deleted_at"`
	SessionID string                 `json:"session_id"`
	Metadata  map[string]interface{} `json:"metadata"`
	// Must be a pointer to allow for null values
	UserID *string `json:"user_id"`
}

type SessionListResponse struct {
	Sessions   []*Session `json:"sessions"`
	TotalCount int        `json:"total_count"`
	RowCount   int        `json:"response_count"`
}

type CreateSessionRequest struct {
	SessionID string `json:"session_id"`
	// Must be a pointer to allow for null values
	UserID   *string                `json:"user_id"`
	Metadata map[string]interface{} `json:"metadata"`
}

type UpdateSessionRequest struct {
	SessionID string                 `json:"session_id"`
	Metadata  map[string]interface{} `json:"metadata"`
}

type SessionManager interface {
	Create(ctx context.Context, session *CreateSessionRequest) (*Session, error)
	Get(ctx context.Context, sessionID string) (*Session, error)
	Update(ctx context.Context, session *UpdateSessionRequest, isPrivileged bool) (*Session, error)
	Delete(ctx context.Context, sessionID string) error
	ListAll(ctx context.Context, cursor int64, limit int) ([]*Session, error)
}
