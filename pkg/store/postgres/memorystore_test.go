package postgres

import (
	"math/rand"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/llms"

	"github.com/getzep/zep/pkg/extractors"

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

	memoryStore, err := NewPostgresMemoryStore(appState, testDB)
	if err != nil {
		panic(err)
	}
	appState.MemoryStore = memoryStore
	extractors.Initialize(appState)

	err = CreateSchema(testCtx, appState, testDB)
	if err != nil {
		panic(err)
	}

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

func TestPutMessages(t *testing.T) {
	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
		{
			Role:     "bot",
			Content:  "Hi there!",
			Metadata: map[string]interface{}{"key": "value"},
		},
	}

	t.Run("insert messages", func(t *testing.T) {
		sessionID := createSession(t)
		resultMessages, err := putMessages(testCtx, testDB, sessionID, messages)
		assert.NoError(t, err, "putMessages should not return an error")

		verifyMessagesInDB(t, messages, resultMessages)
	})

	t.Run("upsert messages with updated TokenCount", func(t *testing.T) {
		sessionID := createSession(t)
		insertedMessages, err := putMessages(testCtx, testDB, sessionID, messages)
		assert.NoError(t, err, "putMessages should not return an error")

		// Update TokenCount values for the returned messages
		for i := range insertedMessages {
			insertedMessages[i].TokenCount = i + 1
		}

		// Call putMessages function to upsert the messages
		upsertedMessages, err := putMessages(testCtx, testDB, sessionID, insertedMessages)
		assert.NoError(t, err, "putMessages should not return an error")

		verifyMessagesInDB(t, insertedMessages, upsertedMessages)
	})

	t.Run(
		"upsert messages with deleted session should not error",
		func(t *testing.T) {
			sessionID := createSession(t)

			insertedMessages, err := putMessages(testCtx, testDB, sessionID, messages)
			assert.NoError(t, err, "putMessages should not return an error")

			sessionStore := NewSessionDAO(testDB)
			err = sessionStore.Delete(testCtx, sessionID)
			assert.NoError(t, err, "deleteSession should not return an error")

			messagesOnceDeleted, err := getMessages(testCtx, testDB, sessionID, 12, nil, 0)
			assert.NoError(t, err, "getMessages should not return an error")

			// confirm that no records were returned
			assert.Equal(t, 0, len(messagesOnceDeleted), "getMessages should return 0 messages")

			// Call putMessages function to upsert the messages
			_, err = putMessages(testCtx, testDB, sessionID, insertedMessages)
			assert.NoError(t, err, "putMessages should not return an error")
		},
	)
}

func createSession(t *testing.T) string {
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

func verifyMessagesInDB(
	t *testing.T,
	expectedMessages,
	resultMessages []models.Message,
) {
	assert.Equal(
		t,
		len(expectedMessages),
		len(resultMessages),
		"Expected number of messages to be equal",
	)
	for i := range expectedMessages {
		assert.NotEmpty(t, resultMessages[i].UUID)
		assert.False(t, resultMessages[i].CreatedAt.IsZero())
		assert.Equal(
			t,
			expectedMessages[i].Role,
			resultMessages[i].Role,
			"Expected message role to be equal",
		)
		assert.Equal(
			t,
			expectedMessages[i].Content,
			resultMessages[i].Content,
			"Expected message content to be equal",
		)
		assert.Equal(
			t,
			expectedMessages[i].Metadata,
			resultMessages[i].Metadata,
			"Expected metadata to be equal",
		)
		assert.Equal(
			t,
			expectedMessages[i].TokenCount,
			resultMessages[i].TokenCount,
			"Expected TokenCount to be equal",
		)
		assert.Equal(
			t,
			expectedMessages[i].Metadata,
			resultMessages[i].Metadata,
			"Expected Metadata to be equal",
		)
		assert.Less(
			t,
			resultMessages[i].CreatedAt,
			resultMessages[i].UpdatedAt,
			"CreatedAt should be less than UpdatedAt",
		)
	}
}

func TestGetMessages(t *testing.T) {
	// Create a test session
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	messages, err := putMessages(testCtx, testDB, sessionID, testutils.TestMessages)
	assert.NoError(t, err)

	expectedMessages := make([]models.Message, len(messages))
	copy(expectedMessages, messages)

	// Explicitly set the message window to 10
	messageWindow := 10
	// Get the index for the last message in the summary
	summaryPointIndex := len(messages) - messageWindow/2 - 1

	tests := []struct {
		name           string
		sessionID      string
		lastNMessages  int
		expectedLength int
		withSummary    bool
	}{
		{
			name:           "Get all messages",
			sessionID:      sessionID,
			lastNMessages:  0,
			expectedLength: len(messages),
			withSummary:    false,
		},
		{
			name:           "Get all messages up to SummaryPoint",
			sessionID:      sessionID,
			lastNMessages:  0,
			expectedLength: 5,
			withSummary:    true,
		},
		{
			name:           "Get last message",
			sessionID:      sessionID,
			lastNMessages:  2,
			expectedLength: 2,
			withSummary:    false,
		},
		{
			name:           "Non-existent session",
			sessionID:      "nonexistent",
			lastNMessages:  0,
			expectedLength: 0,
			withSummary:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var summary *models.Summary
			if tt.withSummary {
				// Create a summary using the test messages. The SummaryPointUUID should be at messageWindow - 2
				summary, err = putSummary(
					testCtx,
					testDB,
					sessionID,
					&models.Summary{Content: "Test summary",
						SummaryPointUUID: messages[summaryPointIndex].UUID},
				)
				assert.NoError(t, err)

				expectedMessages = expectedMessages[len(expectedMessages)-(messageWindow/2):]
			}
			if tt.lastNMessages > 0 {
				expectedMessages = expectedMessages[len(expectedMessages)-tt.lastNMessages:]
			}
			result, err := getMessages(
				testCtx,
				testDB,
				tt.sessionID,
				messageWindow,
				summary,
				tt.lastNMessages,
			)
			assert.NoError(t, err)

			if tt.expectedLength > 0 {
				assert.NotNil(t, result)
				assert.Equal(t, tt.expectedLength, len(result))
				for i, msg := range result {
					expectedMessage := expectedMessages[i]
					assert.NotEmpty(t, msg.UUID)
					assert.False(t, msg.CreatedAt.IsZero())
					assert.Equal(t, expectedMessage.Role, msg.Role)
					assert.Equal(
						t,
						expectedMessage.Content,
						msg.Content,
					)
					assert.True(
						t,
						equivalentMaps(expectedMessage.Metadata, msg.Metadata),
					)
				}
			} else {
				assert.Empty(t, result)
			}
		})
	}
}

func TestGetMessageList(t *testing.T) {
	// Create a test session
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	messages, err := putMessages(testCtx, testDB, sessionID, testutils.TestMessages)
	assert.NoError(t, err)

	expectedMessages := make([]models.Message, len(messages))
	copy(expectedMessages, messages)

	tests := []struct {
		name           string
		sessionID      string
		pageNumber     int
		pageSize       int
		expectedLength int
	}{
		{
			name:           "Existing session",
			sessionID:      sessionID,
			pageNumber:     1,
			pageSize:       10,
			expectedLength: 10,
		},
		{
			name:           "Non-existent session",
			sessionID:      "nonexistent",
			pageNumber:     1,
			pageSize:       10,
			expectedLength: 0,
		},
		{
			name:           "Existing session with pagination",
			sessionID:      sessionID,
			pageNumber:     2,
			pageSize:       10,
			expectedLength: 10,
		},
		// Add more test cases as needed
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := getMessageList(testCtx, testDB, tt.sessionID, tt.pageNumber, tt.pageSize)
			assert.NoError(t, err)

			if tt.expectedLength > 0 {
				assert.NotNil(t, result)
				assert.Equal(t, tt.expectedLength, len(result.Messages))

				expectedDict := make(map[string]bool)
				for _, msg := range expectedMessages {
					expectedDict[msg.Role+msg.Content] = true
				}

				for _, msg := range result.Messages {
					_, exists := expectedDict[msg.Role+msg.Content]
					assert.True(t, exists)
					assert.NotEmpty(t, msg.UUID)
					assert.False(t, msg.CreatedAt.IsZero())
				}
			} else {
				assert.Nil(t, result)
			}
		})
	}
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

func TestPutEmbeddingsLocal(t *testing.T) {
	CleanDB(t, testDB)
	err := CreateSchema(testCtx, appState, testDB)
	assert.NoError(t, err)

	err = MigrateMessageEmbeddingDims(testCtx, testDB, embeddingModel.Dimensions)
	assert.NoError(t, err)

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
	}

	// Call putMessages function
	resultMessages, err := putMessages(testCtx, testDB, sessionID, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	vector := make([]float32, embeddingModel.Dimensions)
	src := rand.NewSource(time.Now().UnixNano())
	r := rand.New(src)
	for i := range vector {
		vector[i] = r.Float32()
	}

	// Create embeddings
	embeddings := []models.MessageEmbedding{
		{
			TextUUID:  resultMessages[0].UUID,
			Text:      resultMessages[0].Content,
			Embedding: vector,
		},
	}

	err = putMessageEmbeddings(testCtx, testDB, sessionID, embeddings)
	assert.NoError(t, err, "putMessageEmbeddings should not return an error")

	// Check for the creation of MessageVectorStoreSchema values
	var pgMemoryVectorStores []MessageVectorStoreSchema
	err = testDB.NewSelect().
		Model(&pgMemoryVectorStores).
		Where("session_id = ?", sessionID).
		Order("uuid ASC").
		Scan(testCtx)
	assert.NoError(t, err)

	assert.Equal(
		t,
		len(embeddings),
		len(pgMemoryVectorStores),
		"Expected number of memory vector records to be equal",
	)
	for i, memVec := range pgMemoryVectorStores {
		assert.Equal(t, sessionID, memVec.SessionID, "Expected sessionID to be equal")
		assert.Equal(
			t,
			embeddings[i].TextUUID,
			memVec.MessageUUID,
			"Expected MessageUUID to be equal",
		)
		assert.True(t, memVec.IsEmbedded, "Expected IsEmbedded to be true")
		assert.Equal(
			t,
			embeddings[0].Embedding,
			memVec.Embedding.Slice(),
			"Expected embedding vector to be equal",
		)
		assert.Equal(t, true, memVec.IsEmbedded, "Expected IsEmbedded to be true")
	}
}

func checkForTable(t *testing.T, testDB *bun.DB, schema interface{}) {
	_, err := testDB.NewSelect().Model(schema).Limit(0).Exec(context.Background())
	require.NoError(t, err)
}
