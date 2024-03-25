package server

import (
	"net/http"
	"os"
	"strings"

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

// ApplyCustomHeaders is a middleware that adds custom headers to the response
func ApplyCustomHeaders(customHeaders map[string]string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			for key, value := range customHeaders {
				actualValue := value
				// Detect and handle sensitive header values originating from environment variables
				if strings.HasPrefix(value, "env:") {
					actualValue = os.Getenv(value[4:])
				}

				// Only add the header if it's not already set, allowing for route-specific overrides
				if w.Header().Get(key) == "" {
					w.Header().Add(key, actualValue)
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}
