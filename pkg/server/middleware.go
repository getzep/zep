package server

import (
	"net/http"

	"github.com/getzep/zep/config"
)

const versionHeader = "X-Zep-Version"

// SendVersion is a middleware that adds the current version to the response
func SendVersion(next http.Handler) http.Handler {
	fn := func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		if w.Header().Get(versionHeader) == "" {
			w.Header().Add(
				versionHeader,
				config.VersionString,
			)
		}
		next.ServeHTTP(w, r.WithContext(ctx))
	}
	return http.HandlerFunc(fn)
}
