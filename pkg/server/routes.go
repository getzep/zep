package server

import (
	"fmt"
	"net/http"
	"time"

	"github.com/getzep/zep/pkg/auth"
	"github.com/getzep/zep/pkg/web"
	"github.com/go-chi/jwtauth/v5"

	httpLogger "github.com/chi-middleware/logrus-logger"
	"github.com/getzep/zep/pkg/models"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

const ReadHeaderTimeout = 5 * time.Second

// Create creates a new HTTP server with the given app state
func Create(appState *models.AppState) *http.Server {
	host := appState.Config.Server.Host
	port := appState.Config.Server.Port
	router := setupRouter(appState)
	return &http.Server{
		Addr:              fmt.Sprintf("%s:%d", host, port),
		Handler:           router,
		ReadHeaderTimeout: ReadHeaderTimeout,
	}
}

// @title						Zep REST-like API
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
	router.Use(middleware.CleanPath)
	router.Use(SendVersion)
	router.Use(middleware.Heartbeat("/healthz"))

	// Only setup web routes if enabled
	if appState.Config.Server.WebEnabled {
		setupWebRoutes(router, appState)
	}

	setupAPIRoutes(router, appState)

	return router
}

func setupWebRoutes(router chi.Router, appState *models.AppState) {
	compressor := middleware.Compress(
		5,
		"text/html",
		"text/css",
		"application/javascript",
		"application/json",
		"image/svg+xml",
	)

	// NotFound handler
	router.NotFound(web.NotFoundHandler())

	// Static handler
	router.Route("/static", func(r chi.Router) {
		// Turn off caching in development mode
		if appState.Config.Development {
			r.Use(middleware.NoCache)
		}
		r.Use(compressor)
		r.Handle("/*", http.FileServer(http.FS(web.StaticFS)))
	})

	// Page handlers
	router.Route("/admin", func(r chi.Router) {
		// Add additional middleware for admin routes
		r.Use(middleware.StripSlashes)
		r.Use(compressor)
		r.Get("/", web.IndexHandler)
		r.Route("/users", func(r chi.Router) {
			r.Get("/", web.GetUserListHandler(appState))
			r.Route("/{userID}", func(r chi.Router) {
				r.Get("/", web.GetUserDetailsHandler(appState))
				r.Post("/", web.PostUserDetailsHandler(appState))
				r.Delete("/", web.DeleteUserHandler(appState))

				r.Route("/session", func(r chi.Router) {
					r.Get("/{sessionID}", web.GetSessionDetailsHandler(appState))
					r.Delete("/{sessionID}", web.DeleteSessionHandler(appState))
				})
			})
		})
		r.Route("/sessions", func(r chi.Router) {
			r.Get("/", web.GetSessionListHandler(appState))
			r.Route("/{sessionID}", func(r chi.Router) {
				r.Get("/", web.GetSessionDetailsHandler(appState))
				r.Delete("/", web.DeleteSessionHandler(appState))
			})
		})
		r.Route("/collections", func(r chi.Router) {
			r.Get("/", web.GetCollectionListHandler(appState))
			r.Route("/{collectionName}", func(r chi.Router) {
				r.Get("/", web.ViewCollectionHandler(appState))
				r.Delete("/", web.DeleteCollectionHandler(appState))
				r.Get("/index", web.IndexCollectionHandler(appState))
			})
		})
		r.Get("/collections", web.GetCollectionListHandler(appState))
		r.Get("/settings", web.GetSettingsHandler(appState))
	})
}

func setupAPIRoutes(router chi.Router, appState *models.AppState) {
	router.Route("/api/v1", func(r chi.Router) {
		// JWT authentication on all API routes
		if appState.Config.Auth.Required {
			log.Info("JWT authentication required")
			r.Use(auth.JWTVerifier(appState.Config))
			r.Use(jwtauth.Authenticator)
		}

		setupSessionRoutes(r, appState)
		setupUserRoutes(r, appState)
		setupCollectionRoutes(r, appState)
	})
}

func setupSessionRoutes(router chi.Router, appState *models.AppState) {
	router.Get("/sessions", GetSessionListHandler(appState))
	router.Post("/sessions", CreateSessionHandler(appState))
	router.Route("/sessions/{sessionId}", func(r chi.Router) {
		r.Get("/", GetSessionHandler(appState))
		r.Patch("/", UpdateSessionHandler(appState))
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
}

func setupUserRoutes(router chi.Router, appState *models.AppState) {
	router.Post("/user", CreateUserHandler(appState))
	router.Get("/user", ListAllUsersHandler(appState))
	router.Route("/user/{userId}", func(r chi.Router) {
		r.Get("/", GetUserHandler(appState))
		r.Patch("/", UpdateUserHandler(appState))
		r.Delete("/", DeleteUserHandler(appState))
		r.Get("/sessions", ListUserSessionsHandler(appState))
	})
}

func setupCollectionRoutes(router chi.Router, appState *models.AppState) {
	router.Get("/collection", GetCollectionListHandler(appState))
	router.Route("/collection/{collectionName}", func(r chi.Router) {
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
}
