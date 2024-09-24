package observability

import "github.com/google/uuid"

type Category string

func (c Category) String() string {
	return string(c)
}

type Service interface {
	CaptureError(msg string, err error, keysAndValues ...any)
	CaptureBreadcrumb(category Category, message string, metadata ...map[string]any)
	LogError(msg string, keysAndValues ...any)
	SetRequestScope(accountUUID, projectUUID uuid.UUID)
}

const (
	Category_Projects     Category = "projects"
	Category_Messages     Category = "messages"
	Category_Users        Category = "users"
	Category_Facts        Category = "facts"
	Category_Accounts     Category = "accounts"
	Category_Sessions     Category = "sessions"
	Category_Auth         Category = "auth"
	Category_AccountStore Category = "account_store"
	Category_ProjectStore Category = "project_store"
	Category_Tasks        Category = "task"
)
