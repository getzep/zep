package server

import (
	"fmt"
	httpLogger "github.com/chi-middleware/logrus-logger"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/spf13/viper"
	"net/http"
	"time"
)

const ReadHeaderTimeout = 5 * time.Second

// Create creates a new HTTP server with the given app state
func Create(appState *models.AppState) *http.Server {
	serverPort := viper.GetInt("server.port")
	router := setupRouter(appState)
	return &http.Server{
		Addr:              fmt.Sprintf(":%d", serverPort),
		Handler:           router,
		ReadHeaderTimeout: ReadHeaderTimeout,
	}
}

// setupRouter creates a new chi router and adds middleware
func setupRouter(appState *models.AppState) *chi.Mux {
	router := chi.NewRouter()
	router.Use(httpLogger.Logger("router", log))
	router.Use(middleware.Recoverer)
	router.Use(middleware.RequestID)
	router.Use(middleware.RealIP)
	router.Use(middleware.Heartbeat("/healthz"))

	router.Route("/v1", func(r chi.Router) {
		r.Route("/sessions/{sessionId}", func(r chi.Router) {
			// Memory-related routes
			r.Route("/memory", func(r chi.Router) {
				r.Get("/", GetMemoryHandler(appState))
				r.Post("/", PostMemoryHandler(appState))
				r.Delete("/", DeleteMemoryHandler(appState))
			})
			// Search-related routes
			r.Route("/search", func(r chi.Router) {
				r.Post("/", RunSearchHandler(appState))
			})
		})
	})

	return router
}
