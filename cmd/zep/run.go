package cmd

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/getzep/zep/pkg/auth"

	"github.com/oiime/logrusbun"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/extractors"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/memorystore"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/server"
)

const (
	ErrMemoryStoreTypeNotSet = "memory_store.type must be set"
	ErrPostgresDSNNotSet     = "memory_store.postgres.dsn must be set"
	MemoryStoreTypePostgres  = "postgres"
)

// run is the entrypoint for the zep server
func run() {
	cfg, err := config.LoadConfig(cfgFile)
	if err != nil {
		log.Fatalf("Error configuring Zep: %s", err)
	}

	handleCLIOptions(cfg)

	log.Infof("Starting zep server version %s", VersionString)

	config.SetLogLevel(cfg)
	appState := NewAppState(cfg)

	// Init the extractors, which will register themselves with the MemoryStore
	extractors.Initialize(appState)

	srv := server.Create(appState)

	log.Infof("Listening on: %s", srv.Addr)
	err = srv.ListenAndServe()
	if err != nil {
		log.Fatal(err)
	}
}

// NewAppState creates an AppState struct from the config file / ENV, initializes the memory store,
// and creates the OpenAI client
func NewAppState(cfg *config.Config) *models.AppState {
	appState := &models.AppState{
		OpenAIClient: llms.NewOpenAIRetryClient(cfg),
		Config:       cfg,
	}

	initializeMemoryStore(appState)
	setupSignalHandler(appState)
	setupPurgeProcessor(context.Background(), appState)

	return appState
}

// handleCLIOptions handles CLI options that don't require the server to run
func handleCLIOptions(cfg *config.Config) {
	if showVersion {
		fmt.Println(VersionString)
		os.Exit(0)
	}
	if generateKey {
		fmt.Println(auth.GenerateJWT(cfg))
		os.Exit(0)
	}
}

// initializeMemoryStore initializes the memory store based on the config file / ENV
func initializeMemoryStore(appState *models.AppState) {
	if appState.Config.MemoryStore.Type == "" {
		log.Fatal(ErrMemoryStoreTypeNotSet)
	}

	switch appState.Config.MemoryStore.Type {
	case MemoryStoreTypePostgres:
		if appState.Config.MemoryStore.Postgres.DSN == "" {
			log.Fatal(ErrPostgresDSNNotSet)
		}
		db := memorystore.NewPostgresConn(appState.Config.MemoryStore.Postgres.DSN)
		if appState.Config.Log.Level == "debug" {
			pgDebugLogging(db)
		}
		memoryStore, err := memorystore.NewPostgresMemoryStore(appState, db)
		if err != nil {
			log.Fatal(err)
		}
		appState.MemoryStore = memoryStore
	default:
		log.Fatal(
			fmt.Sprintf(
				"memory_store.type (%s) is not supported",
				appState.Config.MemoryStore.Type,
			),
		)
	}

	log.Info("Using memory store: ", appState.Config.MemoryStore.Type)
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

// setupSignalHandler sets up a signal handler to close the MemoryStore connection on termination
func setupSignalHandler(appState *models.AppState) {
	signalCh := make(chan os.Signal, 1)
	signal.Notify(signalCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-signalCh
		if err := appState.MemoryStore.Close(); err != nil {
			log.Errorf("Error closing MemoryStore connection: %v", err)
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
