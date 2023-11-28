package postgres

import (
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/tasks"
	"os"
	"reflect"
	"testing"

	"github.com/getzep/zep/internal"
	"github.com/sirupsen/logrus"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/uptrace/bun"

	"context"
)

var testDB *bun.DB
var testCtx context.Context
var appState *models.AppState
var embeddingModel *models.EmbeddingModel

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
	testDB, err = NewPostgresConn(appState)
	if err != nil {
		panic(err)
	}
	testutils.SetUpDBLogging(testDB, logger)

	// Initialize the test context
	testCtx = context.Background()

	err = CreateSchema(testCtx, appState, testDB)
	if err != nil {
		panic(err)
	}

	memoryStore, err := NewPostgresMemoryStore(appState, testDB)
	if err != nil {
		panic(err)
	}
	appState.MemoryStore = memoryStore

	// Set up the task router
	db, err := NewPostgresConnForQueue(appState)
	if err != nil {
		panic(err)
	}
	tasks.RunTaskRouter(testCtx, appState, db)

	embeddingModel = &models.EmbeddingModel{
		Service:    "local",
		Dimensions: 384,
	}
}

func tearDown() {
	// Close the database connection
	if err := testDB.Close(); err != nil {
		panic(err)
	}
	internal.SetLogLevel(logrus.InfoLevel)
}

func createSession(t *testing.T) string {
	t.Helper()
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	sessionManager := NewSessionDAO(testDB)
	session := &models.CreateSessionRequest{
		SessionID: sessionID,
	}
	_, err = sessionManager.Create(testCtx, session)
	assert.NoError(t, err, "putSession should not return an error")

	return sessionID
}

// equate map[string]interface{}(nil) and map[string]interface{}{}
// the latter is returned by the database when a row has no metadata.
// both eval to len == 0
func isNilOrEmpty(m map[string]interface{}) bool {
	return len(m) == 0
}

// equivalentMaps compares two maps for equality. It returns true if both maps
// are nil or empty, or if they non-nil and deepequal.
func equivalentMaps(expected, got map[string]interface{}) bool {
	return (isNilOrEmpty(expected) && isNilOrEmpty(got)) ||
		((reflect.DeepEqual(expected, got)) && (expected != nil && got != nil))
}

func checkForTable(t *testing.T, testDB *bun.DB, schema interface{}) {
	_, err := testDB.NewSelect().Model(schema).Limit(0).Exec(context.Background())
	require.NoError(t, err)
}
