package server

import (
	"fmt"
	"net/http"
	"time"

	httpLogger "github.com/chi-middleware/logrus-logger"
	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/spf13/viper"
)

// http-swagger middleware

const ReadHeaderTimeout = 5 * time.Second

// Create creates a new HTTP server with the given app state
func Create(appState *models.AppState) *http.Server {
	serverPort := viper.GetInt("server.port") // TODO: get from config
	router := setupRouter(appState)
	return &http.Server{
		Addr:              fmt.Sprintf(":%d", serverPort),
		Handler:           router,
		ReadHeaderTimeout: ReadHeaderTimeout,
	}
}

//	@title			Zep Long-term Memory API
//	@description	Zep stores, manages, enriches, and searches long-term memory for conversational AI applications

//	@license.name	Apache 2.0
//	@license.url	http://www.apache.org/licenses/LICENSE-2.0.html

// @BasePath	/apt/v1
// @schemes	http https
func setupRouter(appState *models.AppState) *chi.Mux {
	router := chi.NewRouter()
	router.Use(httpLogger.Logger("router", log))
	router.Use(middleware.Recoverer)
	router.Use(middleware.RequestID)
	router.Use(middleware.RealIP)
	router.Use(middleware.Heartbeat("/healthz"))

	router.Route("/api/v1", func(r chi.Router) {
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
