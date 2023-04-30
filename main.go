package main

import (
	"fmt"
	"log"
	"net/http"
	"sync"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/redis/go-redis/v9"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

func main() {
	loadEnv()
	configureViper()

	cmd := &cobra.Command{
		Use:   "papyrus",
		Short: "Papyrus is an application to manage memory and retrieval",
		Run: func(cmd *cobra.Command, args []string) {
			initConfig(cmd)
		},
	}

	initCobraFlags(cmd)
	err := cmd.Execute()
	if err != nil {
		log.Fatalf("Error executing command: %v", err)
	}

	log.Println("Starting Papyrus")

	openaiClient := createOpenAIClient()
	redisClient := createRedisClient()

	longTermMemory := viper.GetBool("LONG_TERM_MEMORY")
	ensureRedisearchIndexIfEnabled(redisClient, longTermMemory)

	appState := AppState{
		WindowSize:     viper.GetInt64("MAX_WINDOW_SIZE"),
		SessionCleanup: &sync.Map{},
		OpenAIClient:   openaiClient,
		LongTermMemory: longTermMemory,
	}

	router := setupRouter(&appState, redisClient)
	server := createServer(router)

	log.Println("Listening on", server.Addr)
	log.Fatal(server.ListenAndServe())
}

func setupRouter(appState *AppState, redisClient *redis.Client) *chi.Mux {
	router := chi.NewRouter()
	router.Use(middleware.Logger)

	router.Get("/health", handleGetHealth)

	router.Route("/v1", func(r chi.Router) {
		r.Route("/sessions/{sessionId}", func(r chi.Router) {
			r.Get("/memory", getMemoryHandler(appState, redisClient))
			r.Post("/memory", postMemoryHandler(appState, redisClient))
			r.Delete("/memory", deleteMemoryHandler(redisClient))
			r.Post("/retrieval", runRetrievalHandler(appState, redisClient))
		})
	})

	return router
}

func createServer(router *chi.Mux) *http.Server {
	return &http.Server{
		Addr:    fmt.Sprintf(":%d", viper.GetInt("PORT")),
		Handler: router,
	}
}
