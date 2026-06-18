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
// ConflictError (409), or any API error whose message reports that the
// resource already exists.
func isAlreadyExists(err error) bool {
	if err == nil {
		return false
	}

	var conflict *zep.ConflictError
	if errors.As(err, &conflict) {
		return true
	}

	if strings.Contains(strings.ToLower(err.Error()), "already exists") {
		return true
	}

	var apiErr *zepcore.APIError
	if errors.As(err, &apiErr) && apiErr != nil {
		return apiErr.StatusCode == 409
	}
	return false
}
