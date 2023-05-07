package cmd

import (
	"fmt"
	"github.com/danielchalef/zep/pkg/extractors"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memorystore"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/danielchalef/zep/pkg/server"
	"github.com/spf13/viper"
	"os"
	"os/signal"
	"syscall"
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
	//messageWindowSize := viper.GetInt64("memory.message_window")

	appState := &models.AppState{
		OpenAIClient: llms.CreateOpenAIClient(),
		Embeddings: &models.EmbeddingsConfig{
			Model:      viper.GetString("embeddings.model"),
			Dimensions: viper.GetInt64("embeddings.dimensions"),
			Enabled:    viper.GetBool("embeddings.enable"),
		},
	}

	initializeMemoryStore(appState)

	return appState
}

// initializeMemoryStore initializes the memory store based on the config file / ENV
// and sets up a signal handler to close the connection on termination
func initializeMemoryStore(appState *models.AppState) {
	memoryStoreType := viper.GetString("memory_store.type")
	if memoryStoreType == "" {
		log.Fatal("memory_store.type must be set")
	}

	switch memoryStoreType {
	case "postgres":
		pgDSN := viper.GetString("memory_store.postgres.dsn")
		if pgDSN == "" {
			log.Fatal("memory_store.postgres.dsn must be set")
		}
		db := memorystore.NewPostgresConn(pgDSN)
		memoryStore, err := memorystore.NewPostgresMemoryStore(appState, db)
		if err != nil {
			log.Fatal(err)
		}
		appState.MemoryStore = memoryStore
	default:
		log.Fatal(fmt.Sprintf("memory_store.type (%s) is not supported", memoryStoreType))
	}

	log.Info("Using memory store: ", memoryStoreType)

	// Set up a signal handler to close the MemoryStore connection on termination
	signalCh := make(chan os.Signal, 1)
	signal.Notify(signalCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-signalCh
		if err := appState.MemoryStore.Close(); err != nil {
			log.Errorf("Error closing MmemoryStore connection: %v", err)
		}
		os.Exit(0)
	}()
}
