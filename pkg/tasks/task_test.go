package tasks

import (
	"context"
	"os"
	"testing"

	"github.com/getzep/zep/pkg/store/postgres"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"
)

var testDB *bun.DB
var testCtx context.Context
var appState *models.AppState

func TestMain(m *testing.M) {
	setup()
	exitCode := m.Run()
	tearDown()

	os.Exit(exitCode)
}

func setup() {
	// Initialize the test context
	testCtx = context.Background()

	logger := internal.GetLogger()
	internal.SetLogLevel(logrus.DebugLevel)

	appState = &models.AppState{}
	cfg := testutils.NewTestConfig()

	llmClient, err := llms.NewLLMClient(testCtx, cfg)
	if err != nil {
		panic(err)
	}

	appState.LLMClient = llmClient
	appState.Config = cfg

	// Initialize the database connection
	testDB, err = postgres.NewPostgresConn(appState)
	if err != nil {
		panic(err)
	}
	testutils.SetUpDBLogging(testDB, logger)

	memoryStore, err := postgres.NewPostgresMemoryStore(appState, testDB)
	if err != nil {
		panic(err)
	}
	appState.MemoryStore = memoryStore

	documentStore, err := postgres.NewDocumentStore(testCtx, appState, testDB)
	if err != nil {
		panic(err)
	}
	appState.DocumentStore = documentStore
}

func tearDown() {
	// Close the database connection
	if err := testDB.Close(); err != nil {
		panic(err)
	}
	internal.SetLogLevel(logrus.InfoLevel)
}
