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

func ApplyCustomHeaders(customHeaders map[string]string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			for key, value := range customHeaders {
				if w.Header().Get(key) == "" {
					w.Header().Add(key, value)
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}
