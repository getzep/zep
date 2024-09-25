
package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/getzep/zep/api/middleware"
	"github.com/getzep/zep/models"
)

func getMiddleware(appState *models.AppState) []func(http.Handler) http.Handler {
	mw := []func(http.Handler) http.Handler{
		middleware.SecretKeyAuthMiddleware,
	}

	return mw
}

func setupAPIRoutes(router chi.Router, as *models.AppState, mw []func(http.Handler) http.Handler) {
	router.Route("/api/v2", func(r chi.Router) {
		for _, m := range mw {
			r.Use(m)
		}

		setupUserRoutes(r, as)
		setupSessionRoutes(r, as)
		setupFactRoutes(r, as)
	})
}
