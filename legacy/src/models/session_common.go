package models

import (
	"context"
	"time"

	"github.com/google/uuid"
)

type SessionCommon struct {
	UUID      uuid.UUID      `json:"uuid"`
	ID        int64          `json:"id"`
	CreatedAt time.Time      `json:"created_at"`
	UpdatedAt time.Time      `json:"updated_at"`
	DeletedAt *time.Time     `json:"deleted_at"`
	EndedAt   *time.Time     `json:"ended_at"`
	SessionID string         `json:"session_id"`
	Metadata  map[string]any `json:"metadata"`
	// Must be a pointer to allow for null values
	UserID      *string   `json:"user_id"`
	ProjectUUID uuid.UUID `json:"project_uuid"`
}

type SessionListResponse struct {
	Sessions   []*Session `json:"sessions"`
	TotalCount int        `json:"total_count"`
	RowCount   int        `json:"response_count"`
}

type CreateSessionRequestCommon struct {
	// The unique identifier of the session.
	SessionID string `json:"session_id" validate:"required"`
	// The unique identifier of the user associated with the session
	UserID *string `json:"user_id"`
	// The metadata associated with the session.
	Metadata map[string]any `json:"metadata"`
}

type UpdateSessionRequestCommon struct {
	SessionID string `json:"session_id" swaggerignore:"true"`
	// The metadata to update
	Metadata map[string]any `json:"metadata" validate:"required"`
}

type SessionStoreCommon interface {
	Update(ctx context.Context, session *UpdateSessionRequest, isPrivileged bool) (*Session, error)
	Create(ctx context.Context, session *CreateSessionRequest) (*Session, error)
	Get(ctx context.Context, sessionID string) (*Session, error)
	Delete(ctx context.Context, sessionID string) error
	ListAll(ctx context.Context, cursor int64, limit int) ([]*Session, error)
	ListAllOrdered(
		ctx context.Context,
		pageNumber int,
		pageSize int,
		orderBy string,
		asc bool,
	) (*SessionListResponse, error)
}
