package server

import (
	"fmt"
	"net/http"
	"time"

	"github.com/go-chi/jwtauth/v5"

	"github.com/getzep/zep/pkg/auth"

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

// @title						Zep REST API
// @version					0.x
// @license.name				Apache 2.0
// @license.url				http://www.apache.org/licenses/LICENSE-2.0.html
// @BasePath					/api/v1
// @schemes					http https
// @securityDefinitions.apikey	Bearer
// @in							header
// @name						Authorization
// @description				Type "Bearer" followed by a space and JWT token.
func setupRouter(appState *models.AppState) *chi.Mux {
	router := chi.NewRouter()
	router.Use(httpLogger.Logger("router", log))
	router.Use(middleware.Recoverer)
	router.Use(middleware.RequestID)
	router.Use(middleware.RealIP)
	router.Use(SendVersion)
	router.Use(middleware.Heartbeat("/healthz"))

	if appState.Config.Auth.Required {
		log.Info("JWT authentication required")
		router.Use(auth.JWTVerifier(appState.Config))
		router.Use(jwtauth.Authenticator)
	}

	router.Route("/api/v1", func(r chi.Router) {
		// Memory session-related routes
		r.Route("/sessions/{sessionId}", func(r chi.Router) {
			r.Get("/", GetSessionHandler(appState))
			r.Post("/", PostSessionHandler(appState))
			// Memory-related routes
			r.Route("/memory", func(r chi.Router) {
				r.Get("/", GetMemoryHandler(appState))
				r.Post("/", PostMemoryHandler(appState))
				r.Delete("/", DeleteMemoryHandler(appState))
			})
			// Memory search-related routes
			r.Route("/search", func(r chi.Router) {
				r.Post("/", SearchMemoryHandler(appState))
			})
		})
		// Document collection-related routes
		r.Get("/collection", GetCollectionListHandler(appState))
		r.Route("/collection/{collectionName}", func(r chi.Router) {
			r.Post("/", CreateCollectionHandler(appState))
			r.Get("/", GetCollectionHandler(appState))
			r.Delete("/", DeleteCollectionHandler(appState))
			r.Patch("/", UpdateCollectionHandler(appState))

			// Document collection search-related routes
			r.Post("/search", SearchDocumentsHandler(appState))

			// Document collection index-related routes
			r.Post("/index/create", CreateCollectionIndexHandler(appState))

			// Document-related routes
			r.Route("/document", func(r chi.Router) {
				r.Post("/", CreateDocumentsHandler(appState))
				// Single document routes (by UUID)
				r.Route("/uuid/{documentUUID}", func(r chi.Router) {
					r.Get("/", GetDocumentHandler(appState))
					r.Patch("/", UpdateDocumentHandler(appState))
					r.Delete("/", DeleteDocumentHandler(appState))
				})
				// Document list routes
				r.Route("/list", func(r chi.Router) {
					r.Post("/get", GetDocumentListHandler(appState))
					r.Post("/delete", DeleteDocumentListHandler(appState))
					r.Patch("/update", UpdateDocumentListHandler(appState))
				})
			})
		})
	})

	return router
}
