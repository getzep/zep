package cmd

import (
	"fmt"

	"net/http"
	"sync"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/redis/go-redis/v9"
	"github.com/spf13/viper"

	"github.com/danielchalef/zep/pkg/app"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memory"
	"github.com/danielchalef/zep/pkg/routes"
)

const ReadHeaderTimeout = 10

func run() {
	openaiClient := llms.CreateOpenAIClient()
	redisClient := memory.CreateRedisClient()

	longTermMemory := viper.GetBool("LONG_TERM_MEMORY")
	memory.EnsureRedisearchIndexIfEnabled(redisClient, longTermMemory)

	appState := app.AppState{
		WindowSize:     viper.GetInt64("MAX_WINDOW_SIZE"),
		SessionCleanup: &sync.Map{},
		OpenAIClient:   openaiClient,
		LongTermMemory: longTermMemory,
	}

	router := setupRouter(&appState, redisClient)
	server := createServer(router)

	log.Info("Listening on", server.Addr)
	log.Fatal(server.ListenAndServe())
}

func setupRouter(appState *app.AppState, redisClient *redis.Client) *chi.Mux {
	router := chi.NewRouter()
	router.Use(middleware.Logger)

	router.Get("/health", routes.HandleGetHealth)

	router.Route("/v1", func(r chi.Router) {
		r.Route("/sessions/{sessionId}", func(r chi.Router) {
			r.Get("/memory", routes.GetMemoryHandler(appState, redisClient))
			r.Post("/memory", routes.PostMemoryHandler(appState, redisClient))
			r.Delete("/memory", routes.DeleteMemoryHandler(redisClient))
			r.Post("/retrieval", routes.RunRetrievalHandler(appState, redisClient))
		})
	})

	return router
}

func createServer(router *chi.Mux) *http.Server {
	return &http.Server{
		Addr:              fmt.Sprintf(":%d", viper.GetInt("PORT")),
		Handler:           router,
		ReadHeaderTimeout: ReadHeaderTimeout,
	}
}
