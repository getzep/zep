package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/joho/godotenv"
	"github.com/redis/go-redis/v9"
	"github.com/sashabaranov/go-openai"
	"github.com/spf13/viper"
)

func main() {
	err := godotenv.Load()
	if err != nil {
		log.Printf("Warning: .env file not found or unable to load")
	}

	viper.SetEnvPrefix("PAPYRUS")
	viper.AutomaticEnv()

	log.Println("Starting Papyrus 🤘")

	openAIKey := viper.GetString("OPENAI_API_KEY")
	if openAIKey == "" {
		log.Fatal("$OPENAI_API_KEY is not set")
	}
	openaiClient := openai.NewClient(openAIKey)

	redisURL := viper.GetString("REDIS_URL")
	if redisURL == "" {
		log.Fatal("$REDIS_URL is not set")
	}
	redisClient := redis.NewClient(&redis.Options{
		Addr: redisURL,
	})

	longTermMemory := viper.GetBool("LONG_TERM_MEMORY")

	if longTermMemory {
		vectorDimensions := 1536
		distanceMetric := "COSINE"

		err := ensureRedisearchIndex(redisClient, vectorDimensions, distanceMetric)
		if err != nil {
			log.Fatalf("RediSearch index error: %v", err)
		}
	}

	port := viper.GetInt("PORT")
	if port == 0 {
		port = 8000
	}

	windowSize := viper.GetInt64("MAX_WINDOW_SIZE")
	if windowSize == 0 {
		windowSize = 12
	}

	sessionCleanup := &sync.Map{}
	appState := AppState{
		WindowSize:     windowSize,
		SessionCleanup: sessionCleanup,
		OpenAIClient:   openaiClient,
		LongTermMemory: longTermMemory,
	}

	router := chi.NewRouter()
	router.Use(middleware.Logger)

	// Move health route to the root level
	router.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		handleGetHealth(w, r)
	})

	// Create a route group with the version number as the common path prefix.
	router.Route("/v1", func(r chi.Router) {
		r.Route("/sessions/{sessionId}", func(r chi.Router) {
			r.Get("/memory", func(w http.ResponseWriter, r *http.Request) {
				sessionID := chi.URLParam(r, "sessionId")
				handleGetMemory(w, r, &appState, redisClient, sessionID)
			})
			r.Post("/memory", func(w http.ResponseWriter, r *http.Request) {
				sessionID := chi.URLParam(r, "sessionId")
				handlePostMemory(w, r, &appState, redisClient, sessionID)
			})
			r.Delete("/memory", func(w http.ResponseWriter, r *http.Request) {
				sessionID := chi.URLParam(r, "sessionId")
				handleDeleteMemory(w, r, redisClient, sessionID)
			})
		})
		r.Post("/retrieval", func(w http.ResponseWriter, r *http.Request) {
			sessionID := chi.URLParam(r, "sessionId")
			var payload SearchPayload
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			handleRunRetrieval(w, r, sessionID, payload, &appState, redisClient)
		})
	})

	server := http.Server{
		Addr:    fmt.Sprintf(":%d", port),
		Handler: router,
	}

	log.Println("Listening on", server.Addr)
	log.Fatal(server.ListenAndServe())
}
