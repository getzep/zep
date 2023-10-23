package server

import (
	"net/http"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
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

// CustomHeader will take any configured custom headers in the configuration file or
// ZEP_SECRET_CUSTOM_HEADER and  ZEP_SECRET_CUSTOM_HEADER_VALUE environment variables and add them to requests
func CustomHeader(appState *models.AppState) func(http.Handler) http.Handler {
	f := func(next http.Handler) http.Handler {
		fn := func(w http.ResponseWriter, r *http.Request) {
			// Add each non-secret custom header
			for header, value := range appState.Config.Server.CustomHeaders {
				w.Header().Add(header, value)
			}

			// Add the secret custom header if provided
			if appState.Config.Server.SecretCustomHeader != "" && appState.Config.Server.SecretCustomHeaderValue != "" {
				w.Header().Add(
					appState.Config.Server.SecretCustomHeader,
					appState.Config.Server.SecretCustomHeaderValue,
				)
			}

			next.ServeHTTP(w, r.WithContext(r.Context()))
		}
		return http.HandlerFunc(fn)
	}
	return f
}
