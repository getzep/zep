package zepadk

import (
	"errors"
	"strings"

	zep "github.com/getzep/zep-go/v3"
	zepcore "github.com/getzep/zep-go/v3/core"
)

// isAlreadyExists reports whether err indicates that the resource being
// created already exists. EnsureUser and EnsureThread are idempotent and treat
// this as success, so they can be called on every session start without first
// checking for existence.
//
// Zep is not fully consistent about how it signals this: creating a duplicate
// user returns HTTP 400 with a body of "bad request: user already exists ...",
// while other resources may return 409 Conflict. This handles both: any
// ConflictError (409), any API error whose message reports that the resource
// already exists, or -- as a fallback for untyped/legacy error shapes -- any
// error whose message merely says "conflict". This last fallback matches
// Python's _is_already_exists_error and TypeScript's isAlreadyExistsError, so
// all three language integrations treat the same untyped "conflict" error
// message identically instead of Go alone surfacing it as a genuine failure.
func isAlreadyExists(err error) bool {
	if err == nil {
		return false
	}

	var conflict *zep.ConflictError
	if errors.As(err, &conflict) {
		return true
	}

	lower := strings.ToLower(err.Error())
	if strings.Contains(lower, "already exists") {
		return true
	}

	var apiErr *zepcore.APIError
	if errors.As(err, &apiErr) && apiErr != nil {
		return apiErr.StatusCode == 409
	}

	// Fallback for untyped/legacy error shapes that only say "conflict"
	// without a structured status code.
	return strings.Contains(lower, "conflict")
}
