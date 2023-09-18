package models

import (
	"context"
	"time"

	"github.com/google/uuid"
)

type User struct {
	UUID      uuid.UUID              `json:"uuid"`
	ID        int64                  `json:"id"`
	CreatedAt time.Time              `json:"created_at"`
	UpdatedAt time.Time              `json:"updated_at"`
	DeletedAt *time.Time             `json:"deleted_at"`
	UserID    string                 `json:"user_id"`
	Email     string                 `json:"email,omitempty"`
	FirstName string                 `json:"first_name,omitempty"`
	LastName  string                 `json:"last_name,omitempty"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
}

type UserListResponse struct {
	Users      []*User `json:"users"`
	TotalCount int     `json:"total_count"`
	RowCount   int     `json:"row_count"`
}

type CreateUserRequest struct {
	UserID    string                 `json:"user_id"`
	Email     string                 `json:"email"`
	FirstName string                 `json:"first_name"`
	LastName  string                 `json:"last_name"`
	Metadata  map[string]interface{} `json:"metadata"`
}

type UpdateUserRequest struct {
	UUID      uuid.UUID              `json:"uuid"`
	UserID    string                 `json:"user_id"`
	Email     string                 `json:"email"`
	FirstName string                 `json:"first_name"`
	LastName  string                 `json:"last_name"`
	Metadata  map[string]interface{} `json:"metadata"`
}

type UserStore interface {
	Create(ctx context.Context, user *CreateUserRequest) (*User, error)
	Get(ctx context.Context, userID string) (*User, error)
	Update(ctx context.Context, user *UpdateUserRequest, isPrivileged bool) (*User, error)
	Delete(ctx context.Context, userID string) error
	GetSessions(ctx context.Context, userID string) ([]*Session, error)
	ListAll(ctx context.Context, cursor int64, limit int) ([]*User, error)
	ListAllOrdered(ctx context.Context,
		pageNumber int,
		pageSize int,
		orderBy string,
		asc bool,
	) (*UserListResponse, error)
}
