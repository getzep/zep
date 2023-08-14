package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/getzep/zep/pkg/store/postgres"

	"github.com/getzep/zep/pkg/auth"

	"github.com/oiime/logrusbun"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/extractors"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/server"
)

const (
	ErrStoreTypeNotSet   = "store.type must be set"
	ErrPostgresDSNNotSet = "store.postgres.dsn must be set"
	StoreTypePostgres    = "postgres"
)

// run is the entrypoint for the zep server
func run() {
	cfg, err := config.LoadConfig(cfgFile)
	if err != nil {
		log.Fatalf("Error configuring Zep: %s", err)
	}

	handleCLIOptions(cfg)

	log.Infof("Starting zep server version %s", config.VersionString)

	config.SetLogLevel(cfg)
	appState := NewAppState(cfg)

	srv := server.Create(appState)

	log.Infof("Listening on: %s", srv.Addr)
	err = srv.ListenAndServe()
	if err != nil {
		log.Fatal(err)
	}
}

// NewAppState creates an AppState struct from the config file / ENV, initializes the stores,
// extractors, and creates the OpenAI client
func NewAppState(cfg *config.Config) *models.AppState {
	ctx := context.Background()

	// Create a new LLM client
	llmClient, err := llms.NewLLMClient(ctx, cfg)
	if err != nil {
		log.Fatal(err)
	}

	appState := &models.AppState{
		LLMClient: llmClient,
		Config:    cfg,
	}

	initializeStores(appState)

	// Init the extractors, which will register themselves with the MemoryStore
	extractors.Initialize(appState)

	setupSignalHandler(appState)

	setupPurgeProcessor(ctx, appState)

	return appState
}

// handleCLIOptions handles CLI options that don't require the server to run
func handleCLIOptions(cfg *config.Config) {
	if showVersion {
		fmt.Println(config.VersionString)
		os.Exit(0)
	} else if dumpConfig {
		fmt.Println(dumpConfigToJSON(cfg))
		os.Exit(0)
	} else if generateKey {
		fmt.Println(auth.GenerateJWT(cfg))
		os.Exit(0)
	}
}

// initializeStores initializes the memory and document stores based on the config file / ENV
// TODO: refactor to include document store switch
func initializeStores(appState *models.AppState) {
	if appState.Config.Store.Type == "" {
		log.Fatal(ErrStoreTypeNotSet)
	}

	switch appState.Config.Store.Type {
	case StoreTypePostgres:
		if appState.Config.Store.Postgres.DSN == "" {
			log.Fatal(ErrPostgresDSNNotSet)
		}
		db := postgres.NewPostgresConn(appState)
		if appState.Config.Log.Level == "debug" {
			pgDebugLogging(db)
		}
		memoryStore, err := postgres.NewPostgresMemoryStore(appState, db)
		if err != nil {
			log.Fatalf("unable to create memoryStore %v", err)
		}
		log.Debug("memoryStore created")

		// create channels for the document embedding processor
		// TODO: make this a configurable buffer size
		embeddingTaskChannel := make(
			chan []models.DocEmbeddingTask,
			100,
		)
		// TODO: make this a configurable buffer size
		embeddingUpdateChannel := make(chan []models.DocEmbeddingUpdate, 100)
		documentStore, err := postgres.NewDocumentStore(
			appState,
			db,
			embeddingUpdateChannel,
			embeddingTaskChannel,
		)
		if err != nil {
			log.Fatalf("unable to create documentStore: %v", err)
		}
		log.Debug("documentStore created")

		// start the document embedding processor
		embeddingProcessor := extractors.NewDocEmbeddingProcessor(
			appState,
			embeddingTaskChannel,
			embeddingUpdateChannel,
		)
		err = embeddingProcessor.Run(context.Background())
		if err != nil {
			log.Fatalf("unable to start embeddingProcessor: %v", err)
		}
		log.Debug("embeddingProcessor started")

		appState.MemoryStore = memoryStore
		appState.DocumentStore = documentStore
	default:
		log.Fatal(
			fmt.Sprintf(
				"store.type (%s) is not supported",
				appState.Config.Store.Type,
			),
		)
	}

	log.Info("Using memory store: ", appState.Config.Store.Type)
}

func pgDebugLogging(db *bun.DB) {
	db.AddQueryHook(logrusbun.NewQueryHook(logrusbun.QueryHookOptions{
		LogSlow:         time.Second,
		Logger:          log,
		QueryLevel:      logrus.DebugLevel,
		ErrorLevel:      logrus.ErrorLevel,
		SlowLevel:       logrus.WarnLevel,
		MessageTemplate: "{{.Operation}}[{{.Duration}}]: {{.Query}}",
		ErrorTemplate:   "{{.Operation}}[{{.Duration}}]: {{.Query}}: {{.Error}}",
	}))
}

// setupSignalHandler sets up a signal handler to close the store connections and channels on termination
func setupSignalHandler(appState *models.AppState) {
	signalCh := make(chan os.Signal, 1)
	signal.Notify(signalCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-signalCh
		if err := appState.MemoryStore.Close(); err != nil {
			log.Errorf("Error closing MemoryStore connection: %v", err)
		}
		if err := appState.DocumentStore.Shutdown(context.Background()); err != nil {
			log.Errorf("Error shutting down DocumentStore: %v", err)
		}
		os.Exit(0)
	}()
}

// setupPurgeProcessor sets up a go routine to purge deleted records from the MemoryStore
// at a regular interval. It's cancellable via the passed context.
// If Config.DataConfig.PurgeEvery is 0, this function does nothing.
func setupPurgeProcessor(ctx context.Context, appState *models.AppState) {
	interval := time.Duration(appState.Config.DataConfig.PurgeEvery) * time.Minute
	if interval == 0 {
		log.Debug("purge delete processor disabled")
		return
	}

	log.Infof("Starting purge delete processor. Purging every %v", interval)
	go func() {
		for {
			select {
			case <-ctx.Done():
				log.Info("Stopping purge delete processor")
				return
			default:
				err := appState.MemoryStore.PurgeDeleted(ctx)
				if err != nil {
					log.Errorf("error purging deleted records: %v", err)
				}
			}
			time.Sleep(interval)
		}
	}()
}

func dumpConfigToJSON(cfg *config.Config) string {
	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		log.Fatalf("error marshalling config to JSON: %v", err)
	}

	return string(b)
}
