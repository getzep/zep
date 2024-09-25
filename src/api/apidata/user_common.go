package apidata

import (
	"time"

	"github.com/getzep/zep/models"
	"github.com/google/uuid"
)

func UserTransformer(user *models.User) User {
	u := User{
		UserCommon: UserCommon{
			UUID:         user.UUID,
			ID:           user.ID,
			CreatedAt:    user.CreatedAt,
			UpdatedAt:    user.UpdatedAt,
			DeletedAt:    user.DeletedAt,
			UserID:       user.UserID,
			Email:        user.Email,
			FirstName:    user.FirstName,
			LastName:     user.LastName,
			Metadata:     user.Metadata,
			SessionCount: user.SessionCount,
		},
	}

	transformUser(user, &u)

	return u
}

func UserListTransformer(users []*models.User) []User {
	userList := make([]User, len(users))
	for i, user := range users {
		u := user
		userList[i] = UserTransformer(u)
	}
	return userList
}

type UserCommon struct {
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
	Users      []User `json:"users"`
	TotalCount int    `json:"total_count"`
	RowCount   int    `json:"row_count"`
}
