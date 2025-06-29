package api

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	chiMiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"
	"github.com/go-playground/validator/v10"
	"github.com/google/uuid"
	"github.com/riandyrn/otelchi"

	"github.com/getzep/zep/api/apihandlers"
	"github.com/getzep/zep/api/handlertools"
	"github.com/getzep/zep/api/middleware"
	"github.com/getzep/zep/lib/config"
	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/models"
)

const (
	MaxRequestSize       = 5 << 20 // 5MB
	ServerContextTimeout = 30 * time.Second
	ReadHeaderTimeout    = 5 * time.Second
	RouterName           = "zep-api"
)

func Create(as *models.AppState) (*http.Server, error) {
	host := config.Http().Host
	port := config.Http().Port

	mw := getMiddleware(as)

	router, err := setupRouter(as, mw)
	if err != nil {
		return nil, err
	}

	return &http.Server{
		Addr:              fmt.Sprintf("%s:%d", host, port),
		Handler:           router,
		ReadHeaderTimeout: ReadHeaderTimeout,
	}, nil
}

// SetupRouter
//
//	@title						Zep Cloud API
//
//	@version					0.x
//	@host						api.getzep.com
//	@BasePath					/api/v2
//	@schemes					https
//	@securityDefinitions.apikey	Api-Key
//	@in							header
//	@name						Authorization
//
//
//	@description				Type "Api-Key" followed by a space and JWT token.
func setupRouter(as *models.AppState, mw []func(http.Handler) http.Handler) (*chi.Mux, error) {
	validations := map[string]func(fl validator.FieldLevel) bool{
		"alphanumeric_with_underscores": handlertools.AlphanumericWithUnderscores,
		"nonemptystrings":               handlertools.NonEmptyStrings,
	}

	if err := handlertools.RegisterValidations(validations); err != nil {
		return nil, err
	}

	router := chi.NewRouter()
	router.Use(
		cors.Handler(cors.Options{
			AllowOriginFunc: func(_ *http.Request, _ string) bool { return true },
			AllowedMethods:  []string{"GET", "POST", "PUT", "DELETE"},
			AllowedHeaders:  []string{"Authorization"},
		}),
		func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				st := time.Now().UTC()
				resp := chiMiddleware.NewWrapResponseWriter(w, r.ProtoMajor)

				defer func() {
					logger.Info(
						"HTTP Request Served",
						"proto", r.Proto,
						"method", r.Method,
						"path", r.URL.Path,
						"request_id", chiMiddleware.GetReqID(r.Context()),
						"duration", time.Since(st),
						"status", resp.Status(),
						"response_size", resp.BytesWritten(),
					)
				}()

				next.ServeHTTP(resp, r)
			})
		},
		chiMiddleware.Heartbeat("/healthz"),
		chiMiddleware.RequestSize(config.Http().MaxRequestSize),
		func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requestID := r.Header.Get(handlertools.RequestIDHeader)
				if requestID == "" {
					requestID = uuid.New().String()
				}

				ctx := context.WithValue(r.Context(), handlertools.RequestIDKey, requestID) //nolint:staticcheck // it will be fine

				next.ServeHTTP(w, r.WithContext(ctx))
			})
		},
		chiMiddleware.Timeout(ServerContextTimeout),
		chiMiddleware.RealIP,
		chiMiddleware.CleanPath,
		middleware.SendVersion,
		otelchi.Middleware(
			RouterName,
			otelchi.WithChiRoutes(router),
			otelchi.WithRequestMethodInSpanName(true),
		),
	)

	setupAPIRoutes(router, as, mw)

	return router, nil
}

func setupSessionRoutes(router chi.Router, appState *models.AppState, extend ...map[string]func(chi.Router, *models.AppState)) {
	var extensions map[string]func(chi.Router, *models.AppState)
	if len(extend) > 0 {
		extensions = extend[0]
	}

	router.Get("/sessions-ordered", apihandlers.GetOrderedSessionListHandler(appState))

	// these need to be explicitly defined to avoid conflicts with the /sessions/{sessionId} route
	router.Post("/sessions/search", apihandlers.SearchSessionsHandler(appState))

	router.Route("/sessions", func(r chi.Router) {
		r.Get("/", apihandlers.GetSessionListHandler(appState))
		r.Post("/", apihandlers.CreateSessionHandler(appState))

		if ex, ok := extensions["/sessions"]; ok {
			ex(r, appState)
		}
	})

	router.Route("/sessions/{sessionId}", func(r chi.Router) {
		r.Get("/", apihandlers.GetSessionHandler(appState))
		r.Patch("/", apihandlers.UpdateSessionHandler(appState))

		if ex, ok := extensions["/sessions/{sessionId}"]; ok {
			ex(r, appState)
		}

		// Memory-related routes
		r.Route("/memory", func(r chi.Router) {
			r.Get("/", apihandlers.GetMemoryHandler(appState))
			r.Post("/", apihandlers.PostMemoryHandler(appState))
			r.Delete("/", apihandlers.DeleteMemoryHandler(appState))

			if ex, ok := extensions["/sessions/{sessionId}/memory"]; ok {
				ex(r, appState)
			}
		})

		// Message-related routes
		r.Route("/messages", func(r chi.Router) {
			r.Get("/", apihandlers.GetMessagesForSessionHandler(appState))
			r.Route("/{messageUUID}", func(r chi.Router) {
				r.Get("/", apihandlers.GetMessageHandler(appState))
				r.Patch("/", apihandlers.UpdateMessageMetadataHandler(appState))

				if ex, ok := extensions["/sessions/{sessionId}/messages/{messageUUID}"]; ok {
					ex(r, appState)
				}
			})

			if ex, ok := extensions["/sessions/{sessionId}/messages"]; ok {
				ex(r, appState)
			}
		})
	})
}

func setupUserRoutes(router chi.Router, appState *models.AppState) {
	router.Post("/users", apihandlers.CreateUserHandler(appState))
	router.Get("/users", apihandlers.ListAllUsersHandler(appState))
	router.Get("/users-ordered", apihandlers.ListAllOrderedUsersHandler(appState))
	router.Route("/users/{userId}", func(r chi.Router) {
		r.Get("/", apihandlers.GetUserHandler(appState))
		r.Patch("/", apihandlers.UpdateUserHandler(appState))
		r.Delete("/", apihandlers.DeleteUserHandler(appState))
		r.Get("/sessions", apihandlers.ListUserSessionsHandler(appState))
	})
}

func setupFactRoutes(router chi.Router, appState *models.AppState) {
	router.Route("/facts/{factUUID}", func(r chi.Router) {
		r.Get("/", apihandlers.GetFactHandler(appState))
		r.Delete("/", apihandlers.DeleteFactHandler(appState))
	})
}
