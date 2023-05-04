package cmd

import (
	"fmt"
	"github.com/danielchalef/zep/pkg/extractors"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memorystore"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/danielchalef/zep/pkg/server"
	"github.com/redis/go-redis/v9"
	"sync"

	"github.com/spf13/viper"
)

// run is the entrypoint for the zep server
func run() {
	appState := createAppState()

	extractors.Initialize(appState)

	srv := server.Create(appState)

	log.Info("Listening on: ", srv.Addr)
	err := srv.ListenAndServe()
	if err != nil {
		log.Fatal(err)
	}
}

// createAppState creates an AppState struct from the config file / ENV, initializes the memory store,
// and creates the OpenAI client
func createAppState() *models.AppState {
	maxSessionLength := viper.GetInt64("messages.max_session_length")
	messageWindowSize := viper.GetInt64("memory.message_window")

	if maxSessionLength < messageWindowSize {
		log.Fatal(
			fmt.Sprintf(
				"max_session_length (%d) must be greater than message_window (%d)",
				maxSessionLength,
				messageWindowSize,
			),
		)
	}

	memoryStoreType := viper.GetString("memory_store.type")
	if memoryStoreType == "" {
		log.Fatal("memory_store.type must be set")
	}
	memoryStoreURL := viper.GetString("memory_store.url")
	if memoryStoreURL == "" {
		log.Fatal("memory_store.url must be set")
	}

	appState := &models.AppState{
		SessionLock:  &sync.Map{},
		OpenAIClient: llms.CreateOpenAIClient(),
		Embeddings: &models.Embeddings{
			Model:      viper.GetString("embeddings.model"),
			Dimensions: viper.GetInt64("embeddings.dimensions"),
			Enabled:    viper.GetBool("embeddings.enable"),
		},
		MaxSessionLength: maxSessionLength,
	}

	switch memoryStoreType {
	case "redis":
		redisClient := redis.NewClient(&redis.Options{
			Addr: memoryStoreURL,
		})
		memoryStore, err := memorystore.NewRedisMemoryStore(appState, redisClient)
		if err != nil {
			log.Fatal(err)
		}
		appState.MemoryStore = memoryStore
	case "postgres":
		log.Fatal("postgres memory store is not yet supported")
	default:
		log.Fatal(fmt.Sprintf("memory_store.type (%s) is not supported", memoryStoreType))
	}

	log.Info("Using memory store: ", memoryStoreType)

	return appState
}
