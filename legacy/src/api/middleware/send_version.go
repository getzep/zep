package middleware

import (
	"net/http"

	"github.com/getzep/zep/lib/config"
	"github.com/go-chi/chi/v5/middleware"
)

const VersionHeader = "X-Zep-Version"

// SendVersion is a middleware that adds the current version to the response
func SendVersion(next http.Handler) http.Handler {
	fn := func(w http.ResponseWriter, r *http.Request) {
		resp := middleware.NewWrapResponseWriter(w, r.ProtoMajor)

		next.ServeHTTP(resp, r)

		// we want this to run after the request to ensure we aren't overriding any headers
		// that were set by the handler
		if resp.Header().Get(VersionHeader) == "" {
			resp.Header().Add(
				VersionHeader,
				config.VersionString(),
			)
		}
	}

	return http.HandlerFunc(fn)
}
