package server

import (
	"net/http"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
)

const versionHeader = "X-Zep-Version"

// ZepMiddleware implements any custom middlewares for Zep and allows the middlewares to access shared
// resources, such as the models.AppState
type zepCustomMiddleware struct {
	appState *models.AppState
}

func newZepCustomMiddleware(appState *models.AppState) *zepCustomMiddleware {
	return &zepCustomMiddleware{
		appState: appState,
	}
}

// SendVersion is a middleware that adds the current version to the response
func (middleware *zepCustomMiddleware) SendVersion(next http.Handler) http.Handler {
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
// ZEP_CUSTOM_HEADER_SECRET environment variable and add them to requests
func (middleware *zepCustomMiddleware)  CustomHeader(next http.Handler) http.Handler {
	fn := func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		// Add each non-secret custom header
		for header, value := range middleware.appState.Config.Server.CustomHeaders {
			w.Header().Add(header, value)
		}
		// Add each secret custom header
		for header, value := range middleware.appState.Config.Server.SecretCustomHeaders {
			w.Header().Add(header, value)
		}
		next.ServeHTTP(w, r.WithContext(ctx))
	}
	return http.HandlerFunc(fn)
}