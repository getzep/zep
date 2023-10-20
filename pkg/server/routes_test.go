package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/store/postgres"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/require"
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

	embeddingTaskChannel := make(
		chan []models.DocEmbeddingTask,
		10,
	)
	embeddingUpdateChannel := make(chan []models.DocEmbeddingUpdate, 500)
	documentStore, err := postgres.NewDocumentStore(
		appState,
		testDB,
		embeddingUpdateChannel,
		embeddingTaskChannel,
	)
	if err != nil {
		log.Fatalf("unable to create documentStore: %v", err)
	}
	appState.DocumentStore = documentStore

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

func TestAuthMiddleware(t *testing.T) {
	t.Run("auth required", func(t *testing.T) {
		appState := &models.AppState{
			Config: &config.Config{
				Auth: config.AuthConfig{
					Secret:   "test-secret",
					Required: true,
				},
			},
		}

		router := setupRouter(appState)

		req := httptest.NewRequest(http.MethodGet, "/api/v1", nil)
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)
		require.Equal(t, http.StatusUnauthorized, res.Code)
	})

	t.Run("auth not required", func(t *testing.T) {
		appState := &models.AppState{
			Config: &config.Config{
				Auth: config.AuthConfig{
					Secret:   "test-secret",
					Required: false,
				},
			},
		}

		router := setupRouter(appState)

		req := httptest.NewRequest(http.MethodGet, "/api/v1", nil)
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)
		require.Equal(t, http.StatusNotFound, res.Code)
	})
}

func TestSendVersion(t *testing.T) {
	nextHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {})
	zepMiddleware := newZepCustomMiddleware(nil) // SendVersion does not use the appState

	handler := zepMiddleware.SendVersion(nextHandler)

	req, err := http.NewRequest("GET", "/api", nil)
	if err != nil {
		t.Fatal(err)
	}

	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Header().Get(versionHeader) != config.VersionString {
		t.Errorf("handler returned wrong version header: got %v want %v",
			rr.Header().Get(versionHeader), config.VersionString)
	}
}

func TestCustomHeader(t *testing.T) {
	nextHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {})
	appState := &models.AppState{
		Config: &config.Config{
			Server: config.ServerConfig{
				CustomHeaders:       map[string]string{
					"custom-header-1": "custom-header-1-value",
					"custom-header-2": "custom-header-2-value",
				},
				SecretCustomHeaders: map[string]string{
					"secret-custom-header-1": "secret-custom-header-1-value",
				},
			},
		},
	}
	zepMiddleware := newZepCustomMiddleware(appState)

	handler := zepMiddleware.CustomHeader(nextHandler)

	req, err := http.NewRequest("GET", "/api", nil)
	if err != nil {
		t.Fatal(err)
	}

	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	for header, value := range zepMiddleware.appState.Config.Server.CustomHeaders {
		if rr.Header().Get(header) != value {
			t.Errorf("handler returned wrong custom header: got %v want %v",
				rr.Header().Get(header), value)
		}
	}
	for header, value := range zepMiddleware.appState.Config.Server.SecretCustomHeaders {
		if rr.Header().Get(header) != value {
			t.Errorf("handler returned wrong secret custom header: got %v want %v",
				rr.Header().Get(header), value)
		}
	}
}
