package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store/postgres"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/go-chi/chi/v5"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"

	"github.com/google/uuid"

	"github.com/stretchr/testify/assert"
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
	appState.Config.Store.Postgres.DSN = testutils.GetDSN()

	// Initialize the database connection
	testDB = postgres.NewPostgresConn(appState)
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

func TestExtractQueryStringValueToInt(t *testing.T) {
	req := httptest.NewRequest("GET", "/?param=123", nil)
	got, err := extractQueryStringValueToInt[int](req, "param")
	assert.NoError(t, err, "extractQueryStringValueToInt() error = %v", err)
	assert.Equal(t, 123, got, "extractQueryStringValueToInt() = %v, want %v", got, 123)
}

func TestParseUUIDFromURL(t *testing.T) {
	r := chi.NewRouter()
	r.Get("/{uuid}", func(w http.ResponseWriter, r *http.Request) {
		urlUUID := parseUUIDFromURL(r, w, "uuid")
		assert.NotNil(t, urlUUID)
	})

	ts := httptest.NewServer(r)
	defer ts.Close()

	// Test with valid UUID
	validUUID := uuid.New()
	res, err := http.Get(ts.URL + "/" + validUUID.String())
	assert.NoError(t, err)
	assert.Equal(t, http.StatusOK, res.StatusCode)

	// Test with invalid UUID
	res, err = http.Get(ts.URL + "/invalid_uuid")
	assert.NoError(t, err)
	assert.Equal(t, http.StatusBadRequest, res.StatusCode)
}
