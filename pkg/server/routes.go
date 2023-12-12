package server

import (
	"fmt"
	"net/http"
	"time"

	"github.com/getzep/zep/pkg/web"
	"github.com/riandyrn/otelchi"

	"github.com/getzep/zep/internal"

	"github.com/getzep/zep/pkg/auth"
	"github.com/getzep/zep/pkg/server/apihandlers"
	"github.com/getzep/zep/pkg/server/webhandlers"
	"github.com/go-chi/jwtauth/v5"

	httpLogger "github.com/chi-middleware/logrus-logger"
	"github.com/getzep/zep/pkg/models"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

const ReadHeaderTimeout = 5 * time.Second
const RouterName = "router"

var log = internal.GetLogger()

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
	maxRequestSize := appState.Config.Server.MaxRequestSize
	if maxRequestSize == 0 {
		maxRequestSize = 5 << 20 // 5MB
	}

	router := chi.NewRouter()
	router.Use(
		httpLogger.Logger(RouterName, log),
		otelchi.Middleware(
			RouterName,
			otelchi.WithChiRoutes(router),
			otelchi.WithRequestMethodInSpanName(true),
		),
		middleware.RequestSize(maxRequestSize),
		middleware.Recoverer,
		middleware.RequestID,
		middleware.RealIP,
		middleware.CleanPath,
		SendVersion,
		middleware.Heartbeat("/healthz"),
	)

	// Only setup web routes if enabled
	if appState.Config.Server.WebEnabled {
		log.Info("Web interface enabled")
		setupWebRoutes(router, appState)
	} else {
		log.Info("Web interface disabled")
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
	router.NotFound(webhandlers.NotFoundHandler())

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
		r.Get("/", webhandlers.IndexHandler)
		r.Route("/users", func(r chi.Router) {
			r.Get("/", webhandlers.GetUserListHandler(appState))
			r.Route("/{userID}", func(r chi.Router) {
				r.Get("/", webhandlers.GetUserDetailsHandler(appState))
				r.Post("/", webhandlers.PostUserDetailsHandler(appState))
				r.Delete("/", webhandlers.DeleteUserHandler(appState))

				r.Route("/session", func(r chi.Router) {
					r.Get("/{sessionID}", webhandlers.GetSessionDetailsHandler(appState))
					r.Delete("/{sessionID}", webhandlers.DeleteSessionHandler(appState))
				})
			})
		})
		r.Route("/sessions", func(r chi.Router) {
			r.Get("/", webhandlers.GetSessionListHandler(appState))
			r.Route("/{sessionID}", func(r chi.Router) {
				r.Get("/", webhandlers.GetSessionDetailsHandler(appState))
				r.Delete("/", webhandlers.DeleteSessionHandler(appState))
			})
		})
		r.Route("/collections", func(r chi.Router) {
			r.Get("/", webhandlers.GetCollectionListHandler(appState))
			r.Route("/{collectionName}", func(r chi.Router) {
				r.Get("/", webhandlers.ViewCollectionHandler(appState))
				r.Delete("/", webhandlers.DeleteCollectionHandler(appState))
				r.Get("/index", webhandlers.IndexCollectionHandler(appState))
			})
		})
		r.Get("/collections", webhandlers.GetCollectionListHandler(appState))
		r.Get("/settings", webhandlers.GetSettingsHandler(appState))
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
	router.Get("/sessions", apihandlers.GetSessionListHandler(appState))
	router.Post("/sessions", apihandlers.CreateSessionHandler(appState))
	router.Route("/sessions/{sessionId}", func(r chi.Router) {
		r.Get("/", apihandlers.GetSessionHandler(appState))
		r.Patch("/", apihandlers.UpdateSessionHandler(appState))
		// Memory-related routes
		r.Route("/memory", func(r chi.Router) {
			r.Get("/", apihandlers.GetMemoryHandler(appState))
			r.Post("/", apihandlers.PostMemoryHandler(appState))
			r.Delete("/", apihandlers.DeleteMemoryHandler(appState))
		})

		// Message-related routes
		r.Route("/messages", func(r chi.Router) {
			r.Get("/", apihandlers.GetMessagesForSessionHandler(appState))
			r.Route("/{messageId}", func(r chi.Router) {
				r.Get("/", apihandlers.GetMessageHandler(appState))
				r.Patch("/", apihandlers.UpdateMessageMetadataHandler(appState))
			})
		})

		// Memory search-related routes
		r.Route("/search", func(r chi.Router) {
			r.Post("/", apihandlers.SearchMemoryHandler(appState))
		})
	})
}

func setupUserRoutes(router chi.Router, appState *models.AppState) {
	router.Post("/user", apihandlers.CreateUserHandler(appState))
	router.Get("/user", apihandlers.ListAllUsersHandler(appState))
	router.Route("/user/{userId}", func(r chi.Router) {
		r.Get("/", apihandlers.GetUserHandler(appState))
		r.Patch("/", apihandlers.UpdateUserHandler(appState))
		r.Delete("/", apihandlers.DeleteUserHandler(appState))
		r.Get("/sessions", apihandlers.ListUserSessionsHandler(appState))
	})
}

func setupCollectionRoutes(router chi.Router, appState *models.AppState) {
	router.Get("/collection", apihandlers.GetCollectionListHandler(appState))
	router.Route("/collection/{collectionName}", func(r chi.Router) {
		r.Post("/", apihandlers.CreateCollectionHandler(appState))
		r.Get("/", apihandlers.GetCollectionHandler(appState))
		r.Delete("/", apihandlers.DeleteCollectionHandler(appState))
		r.Patch("/", apihandlers.UpdateCollectionHandler(appState))

		// Document collection search-related routes
		r.Post("/search", apihandlers.SearchDocumentsHandler(appState))

		// Document collection index-related routes
		r.Post("/index/create", apihandlers.CreateCollectionIndexHandler(appState))

		// Document-related routes
		r.Route("/document", func(r chi.Router) {
			r.Post("/", apihandlers.CreateDocumentsHandler(appState))
			// Single document routes (by UUID)
			r.Route("/uuid/{documentUUID}", func(r chi.Router) {
				r.Get("/", apihandlers.GetDocumentHandler(appState))
				r.Patch("/", apihandlers.UpdateDocumentHandler(appState))
				r.Delete("/", apihandlers.DeleteDocumentHandler(appState))
			})
			// Document list routes
			r.Route("/list", func(r chi.Router) {
				r.Post("/get", apihandlers.GetDocumentListHandler(appState))
				r.Post("/delete", apihandlers.DeleteDocumentListHandler(appState))
				r.Patch("/update", apihandlers.UpdateDocumentListHandler(appState))
			})
		})
	})
}
