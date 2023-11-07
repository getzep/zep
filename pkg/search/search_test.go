package search

import (
	"context"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store/postgres"
	"github.com/getzep/zep/pkg/tasks"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"
)

var testDB *bun.DB
var testCtx context.Context
var appState *models.AppState
var testUserStore models.UserStore
var testServer *httptest.Server

func TestMain(m *testing.M) {
	setup()
	exitCode := m.Run()
	tearDown()

	os.Exit(exitCode)
}

func setup() {
	logger := internal.GetLogger()
	internal.SetLogLevel(logrus.DebugLevel)

	appState = &models.AppState{}
	cfg := testutils.NewTestConfig()

	llmClient, err := llms.NewLLMClient(context.Background(), cfg)
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

	// Initialize the test context
	testCtx = context.Background()

	memoryStore, err := postgres.NewPostgresMemoryStore(appState, testDB)
	if err != nil {
		panic(err)
	}
	appState.MemoryStore = memoryStore

	testUserStore = postgres.NewUserStoreDAO(testDB)
	appState.UserStore = testUserStore

	documentStore, err := postgres.NewDocumentStore(
		testCtx,
		appState,
		testDB,
	)
	if err != nil {
		log.Fatalf("unable to create documentStore: %v", err)
	}
	appState.DocumentStore = documentStore

	// Set up the task router
	db, err := postgres.NewPostgresConnForQueue(appState)
	if err != nil {
		panic(err)
	}
	tasks.RunTaskRouter(testCtx, appState, db)

	testServer = httptest.NewServer(
		setupRouter(appState),
	)
}

func tearDown() {
	testServer.Close()

	// Close the database connection
	if err := testDB.Close(); err != nil {
		panic(err)
	}

	internal.SetLogLevel(logrus.InfoLevel)
}
