package middleware

type ZepContextKey string

const (
	UserId    ZepContextKey = "user_id"
	ProjectId ZepContextKey = "project_id"

	RequestTokenType ZepContextKey = "request_token_type"
)

const BearerRequestTokenType = "bearer"

const (
	apiKeyAuthorizationPrefix = "Api-Key"
)
