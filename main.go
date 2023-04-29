package main

import (
	"fmt"
	"log"
	"net/http"
	"sync"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/redis/go-redis/v9"
	"github.com/sashabaranov/go-openai"
	"github.com/spf13/viper"
)

var (
	healthCheckHandler  http.HandlerFunc
	getMemoryHandler    http.HandlerFunc
	postMemoryHandler   http.HandlerFunc
	deleteMemoryHandler http.HandlerFunc
	runRetrievalHandler http.HandlerFunc
)

func main() {
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

	sessionCleanup := sync.Map{}
	sessionState := AppState{
		WindowSize:     windowSize,
		SessionCleanup: sessionCleanup,
		OpenAIClient:   openaiClient,
		LongTermMemory: longTermMemory,
	}

	router := chi.NewRouter()
	router.Use(middleware.Logger)
	router.Handle("/health", healthCheckHandler)
	router.Handle("/memory", getMemoryHandler)
	router.Handle("/memory", postMemoryHandler)
	router.Handle("/memory", deleteMemoryHandler)
	router.Handle("/retrieval", runRetrievalHandler)

	server := http.Server{
		Addr:    fmt.Sprintf(":%d", port),
		Handler: router,
	}

	log.Println("Listening on", server.Addr)
	log.Fatal(server.ListenAndServe())
}
