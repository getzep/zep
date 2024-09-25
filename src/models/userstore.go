package models

import (
	"context"
	"time"

	"github.com/google/uuid"
)

type User struct {
	UUID         uuid.UUID      `json:"uuid"`
	ID           int64          `json:"id"`
	CreatedAt    time.Time      `json:"created_at"`
	UpdatedAt    time.Time      `json:"updated_at"`
	DeletedAt    *time.Time     `json:"deleted_at"`
	UserID       string         `json:"user_id"`
	Email        string         `json:"email,omitempty"`
	FirstName    string         `json:"first_name,omitempty"`
	LastName     string         `json:"last_name,omitempty"`
	ProjectUUID  uuid.UUID      `json:"project_uuid"`
	Metadata     map[string]any `json:"metadata,omitempty"`
	SessionCount int            `json:"session_count,omitempty"`
}

type UserListResponse struct {
	Users      []*User `json:"users"`
	TotalCount int     `json:"total_count"`
	RowCount   int     `json:"row_count"`
}

type CreateUserRequest struct {
	// The unique identifier of the user.
	UserID string `json:"user_id"`
	// The email address of the user.
	Email string `json:"email"`
	// The first name of the user.
	FirstName string `json:"first_name"`
	// The last name of the user.
	LastName string `json:"last_name"`
	// The metadata associated with the user.
	Metadata map[string]any `json:"metadata"`
}

type UpdateUserRequest struct {
	UUID   uuid.UUID `json:"uuid" swaggerignore:"true"`
	UserID string    `json:"user_id" swaggerignore:"true"`
	// The email address of the user.
	Email string `json:"email"`
	// The first name of the user.
	FirstName string `json:"first_name"`
	// The last name of the user.
	LastName string `json:"last_name"`
	// The metadata to update
	Metadata map[string]any `json:"metadata"`
}

type UserStore interface {
	Create(ctx context.Context, user *CreateUserRequest) (*User, error)
	Get(ctx context.Context, userID string) (*User, error)
	Update(ctx context.Context, user *UpdateUserRequest, isPrivileged bool) (*User, error)
	Delete(ctx context.Context, userID string) error
	GetSessionsForUser(ctx context.Context, userID string) ([]*Session, error)
	ListAll(ctx context.Context, cursor int64, limit int) ([]*User, error)
	ListAllOrdered(ctx context.Context,
		pageNumber int,
		pageSize int,
		orderBy string,
		asc bool,
	) (*UserListResponse, error)
}
