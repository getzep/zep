package memorystore

import (
	"fmt"
	"math/rand"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/extractors"
	"github.com/getzep/zep/pkg/llms"

	"github.com/getzep/zep/internal"
	"github.com/sirupsen/logrus"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/test"
	"github.com/google/uuid"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/uptrace/bun"

	"context"
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
	internal.SetLogLevel(logrus.DebugLevel)
	// Initialize the database connection
	testDB = NewPostgresConn(test.GetDSN())

	// Initialize the test context
	testCtx = context.Background()

	cfg := test.NewTestConfig()

	appState = &models.AppState{}
	appState.OpenAIClient = llms.CreateOpenAIClient(cfg)
	appState.Config = cfg
	store, err := NewPostgresMemoryStore(appState, testDB)
	if err != nil {
		panic(err)
	}
	appState.MemoryStore = store
	extractors.Initialize(appState)

	err = ensurePostgresSetup(testCtx, testDB)
	if err != nil {
		panic(err)
	}
}

func tearDown() {
	// Close the database connection
	if err := testDB.Close(); err != nil {
		panic(err)
	}
	internal.SetLogLevel(logrus.InfoLevel)
}

func TestEnsurePostgresSchemaSetup(t *testing.T) {
	CleanDB(t, testDB)

	t.Run("should succeed when all schema setup is successful", func(t *testing.T) {
		err := ensurePostgresSetup(testCtx, testDB)
		assert.NoError(t, err)

		checkForTable(t, testDB, &PgSession{})
		checkForTable(t, testDB, &PgMessageStore{})
		checkForTable(t, testDB, &PgSummaryStore{})
		checkForTable(t, testDB, &PgMessageVectorStore{})
	})
	t.Run("should not fail on second run", func(t *testing.T) {
		err := ensurePostgresSetup(testCtx, testDB)
		assert.NoError(t, err)
	})
}

func TestPutSession(t *testing.T) {
	tests := []struct {
		name       string
		sessionID  string
		metadata   map[string]interface{}
		wantErr    bool
		errMessage string
	}{
		{
			name:      "Valid session",
			sessionID: "123abc",
			metadata: map[string]interface{}{
				"key": "value",
			},
			wantErr: false,
		},
		{
			name:      "duplicate session id should upsert",
			sessionID: "123abc",
			metadata: map[string]interface{}{
				"key":  "value",
				"key2": "value2",
			},
			wantErr: false,
		},
		{
			name:      "Empty session ID",
			sessionID: "",
			metadata: map[string]interface{}{
				"key": "value",
			},
			wantErr:    true,
			errMessage: "sessionID cannot be empty",
		},
		// Add more test cases as needed
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := putSession(testCtx, testDB, tt.sessionID, tt.metadata)

			if tt.wantErr {
				assert.Error(t, err)
				storageErr, ok := err.(*StorageError)
				if ok {
					assert.Equal(t, tt.errMessage, storageErr.message)
				}
			} else {
				assert.NoError(t, err)
				assert.NotNil(t, result)
				assert.NotEmpty(t, result.UUID)
				assert.False(t, result.CreatedAt.IsZero())
				assert.Equal(t, tt.sessionID, result.SessionID)
				assert.Equal(t, tt.metadata, result.Metadata)
			}
		})
	}
}

func TestGetSession(t *testing.T) {
	// Create a test session
	sessionID := "123abc"
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err := putSession(testCtx, testDB, sessionID, metadata)
	assert.NoError(t, err)

	tests := []struct {
		name          string
		sessionID     string
		expectedFound bool
	}{
		{
			name:          "Existing session",
			sessionID:     "123abc",
			expectedFound: true,
		},
		{
			name:          "Non-existent session",
			sessionID:     "nonexistent",
			expectedFound: false,
		},
		// Add more test cases as needed
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := getSession(testCtx, testDB, tt.sessionID)
			assert.NoError(t, err)

			if tt.expectedFound {
				assert.NotNil(t, result)
				assert.NotEmpty(t, result.UUID)
				assert.False(t, result.CreatedAt.IsZero())
				assert.Equal(t, tt.sessionID, result.SessionID)
				assert.Equal(t, metadata, result.Metadata)
			} else {
				assert.Nil(t, result)
			}
		})
	}
}

func TestPgDeleteSession(t *testing.T) {
	memoryWindow := 10
	appState.Config.Memory.MessageWindow = memoryWindow

	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(testCtx, testDB, sessionID, map[string]interface{}{})
	assert.NoError(t, err, "putSession should not return an error")

	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
		{
			Role:     "bot",
			Content:  "Hi there!",
			Metadata: map[string]interface{}{"timestamp": 1629462551},
		},
	}

	// Call putMessages function
	resultMessages, err := putMessages(testCtx, testDB, sessionID, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	// Put a summary
	summary := models.Summary{
		Content: "This is a summary",
		Metadata: map[string]interface{}{
			"timestamp": 1629462551,
		},
		SummaryPointUUID: resultMessages[0].UUID,
	}
	_, err = putSummary(testCtx, testDB, sessionID, &summary)
	assert.NoError(t, err, "putSummary should not return an error")

	err = deleteSession(testCtx, testDB, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	// Test that session is deleted
	resp, err := getSession(testCtx, testDB, sessionID)
	assert.NoError(t, err, "getSession should not return an error")
	assert.Nil(t, resp, "getSession should return nil")

	// Test that messages are deleted
	respMessages, err := getMessages(testCtx, testDB, sessionID, memoryWindow, nil, 0)
	assert.NoError(t, err, "getMessages should not return an error")
	assert.Nil(t, respMessages, "getMessages should return nil")

	// Test that summary is deleted
	respSummary, err := getSummary(testCtx, testDB, sessionID)
	assert.NoError(t, err, "getSummary should not return an error")
	assert.Nil(t, respSummary, "getSummary should return nil")
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
		"upsert messages with deleted session should error",
		func(t *testing.T) {
			sessionID := createSession(t)

			insertedMessages, err := putMessages(testCtx, testDB, sessionID, messages)
			assert.NoError(t, err, "putMessages should not return an error")

			// Delete using deleteSession
			err = deleteSession(testCtx, testDB, sessionID)
			assert.NoError(t, err, "deleteSession should not return an error")

			messagesOnceDeleted, err := getMessages(testCtx, testDB, sessionID, 12, nil, 0)
			assert.NoError(t, err, "getMessages should not return an error")

			// confirm that no records were returned
			assert.Equal(t, 0, len(messagesOnceDeleted), "getMessages should return 0 messages")

			// Call putMessages function to upsert the messages
			_, err = putMessages(testCtx, testDB, sessionID, insertedMessages)
			assert.ErrorContains(
				t,
				err,
				"deleted",
				"putMessages should return SessionDeletedError",
			)
		},
	)
}

func createSession(t *testing.T) string {
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(testCtx, testDB, sessionID, map[string]interface{}{})
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
	}
}

func TestGetMessages(t *testing.T) {
	// Create a test session
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	messages, err := putMessages(testCtx, testDB, sessionID, test.TestMessages)
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

func TestPutSummary(t *testing.T) {
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(testCtx, testDB, sessionID, map[string]interface{}{})
	assert.NoError(t, err, "putSession should not return an error")

	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
		{
			Role:     "bot",
			Content:  "Hi there!",
			Metadata: map[string]interface{}{"timestamp": 1629462551},
		},
	}

	// Call putMessages function
	resultMessages, err := putMessages(testCtx, testDB, sessionID, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	tests := []struct {
		name             string
		sessionID        string
		summary          models.Summary
		SummaryPointUUID uuid.UUID
		wantErr          bool
		errMessage       string
	}{
		{
			name:      "Valid summary",
			sessionID: sessionID,
			summary: models.Summary{
				Content: "Test content",
				Metadata: map[string]interface{}{
					"key": "value",
				},
				SummaryPointUUID: resultMessages[0].UUID,
			},

			wantErr: false,
		},
		{
			name:      "Empty session ID",
			sessionID: "",
			summary: models.Summary{
				Content: "Test content",
				Metadata: map[string]interface{}{
					"key": "value",
				},
				SummaryPointUUID: resultMessages[1].UUID,
			},

			wantErr:    true,
			errMessage: "sessionID cannot be empty",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resultSummary, err := putSummary(
				testCtx,
				testDB,
				tt.sessionID,
				&tt.summary,
			)

			if tt.wantErr {
				assert.Error(t, err)
				storageErr, ok := err.(*StorageError)
				if ok {
					assert.Equal(t, tt.errMessage, storageErr.message)
				}
			} else {
				assert.NoError(t, err)
				assert.NotNil(t, resultSummary)
				assert.NotEmpty(t, resultSummary.UUID)
				assert.False(t, resultSummary.CreatedAt.IsZero())
				assert.Equal(t, tt.summary.Content, resultSummary.Content)
				assert.Equal(t, tt.summary.Metadata, resultSummary.Metadata)
			}
		})
	}
}

func TestGetSummary(t *testing.T) {
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(testCtx, testDB, sessionID, metadata)
	assert.NoError(t, err)

	summary := models.Summary{
		Content: "Test content",
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}
	summaryTwo := models.Summary{
		Content: "Test content 2",
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
		{
			Role:     "bot",
			Content:  "Hello!",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
	}

	// Call putMessages function
	resultMessages, err := putMessages(testCtx, testDB, sessionID, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	summary.SummaryPointUUID = resultMessages[0].UUID
	_, err = putSummary(testCtx, testDB, sessionID, &summary)
	assert.NoError(t, err, "putSummary should not return an error")

	summaryTwo.SummaryPointUUID = resultMessages[1].UUID
	putSummaryResultTwo, err := putSummary(testCtx, testDB, sessionID, &summaryTwo)
	assert.NoError(t, err, "putSummary2 should not return an error")

	tests := []struct {
		name          string
		sessionID     string
		expectedFound bool
	}{
		{
			name:          "Existing summary",
			sessionID:     sessionID,
			expectedFound: true,
		},
		{
			name:          "Non-existent session",
			sessionID:     "nonexistent",
			expectedFound: false,
		},
		// Add more test cases as needed
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := getSummary(testCtx, testDB, tt.sessionID)
			assert.NoError(t, err)

			if tt.expectedFound {
				assert.NotNil(t, result)
				// Ensure it is the last summary added
				assert.Equal(t, putSummaryResultTwo.UUID, result.UUID)
				assert.False(t, result.CreatedAt.IsZero())
				assert.Equal(t, putSummaryResultTwo.Content, result.Content)
				assert.Equal(t, putSummaryResultTwo.Metadata, result.Metadata)
			} else {
				assert.Nil(t, result)
			}
		})
	}
}

func TestPutEmbeddings(t *testing.T) {
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(testCtx, testDB, sessionID, map[string]interface{}{})
	assert.NoError(t, err, "putSession should not return an error")

	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
	}

	// Force embedding to be enabled
	viper.Set("extractor.embeddings.enabled", true)

	// Call putMessages function
	resultMessages, err := putMessages(testCtx, testDB, sessionID, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	vector := make([]float32, 1536)
	src := rand.NewSource(time.Now().UnixNano())
	r := rand.New(src)
	for i := range vector {
		vector[i] = r.Float32()
	}

	// Create embeddings
	embeddings := []models.Embeddings{
		{
			TextUUID:  resultMessages[0].UUID,
			Text:      resultMessages[0].Content,
			Embedding: vector,
		},
	}

	err = putEmbeddings(testCtx, testDB, sessionID, embeddings)
	assert.NoError(t, err, "putEmbeddings should not return an error")

	// Check for the creation of PgMessageVectorStore values
	var pgMemoryVectorStores []PgMessageVectorStore
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

func TestSearch(t *testing.T) {
	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	// Call putMessages function
	msgs, err := putMessages(testCtx, testDB, sessionID, test.TestMessages)
	assert.NoError(t, err, "putMessages should not return an error")

	appState.MemoryStore.NotifyExtractors(
		context.Background(),
		appState,
		&models.MessageEvent{SessionID: sessionID,
			Messages: msgs},
	)

	// enrichment runs async. Wait for it to finish
	// This is hacky but I'd prefer not to add a WaitGroup to the putMessages function just for testing purposes
	time.Sleep(time.Second * 4)

	// Test cases
	testCases := []struct {
		name              string
		query             string
		limit             int
		expectedErrorText string
	}{
		{"Empty Query", "", 0, "empty query"},
		{"Non-empty Query", "travel", 0, ""},
		{"Limit 0", "travel", 0, ""},
		{"Limit 5", "travel", 5, ""},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			q := models.SearchPayload{Text: tc.query}
			expectedLastN := tc.limit
			if expectedLastN == 0 {
				expectedLastN = 10 // Default value
			}

			s, err := searchMessages(testCtx, appState, testDB, sessionID, &q, expectedLastN)

			if tc.expectedErrorText != "" {
				assert.ErrorContains(
					t,
					err,
					tc.expectedErrorText,
					"searchMessages should return the expected error",
				)
			} else {
				assert.NoError(t, err, "searchMessages should not return an error")
				assert.Len(t, s, expectedLastN, fmt.Sprintf("Expected %d messages to be returned", expectedLastN))

				for _, res := range s {
					assert.NotNil(t, res.Message.UUID, "message__uuid should be present")
					assert.NotNil(t, res.Message.CreatedAt, "message__created_at should be present")
					assert.NotNil(t, res.Message.Role, "message__role should be present")
					assert.NotNil(t, res.Message.Content, "message__content should be present")
					assert.NotZero(t, res.Message.TokenCount, "message_token_count should be present")
				}
			}
		})
	}
}

func checkForTable(t *testing.T, testDB *bun.DB, schema interface{}) {
	_, err := testDB.NewSelect().Model(schema).Limit(0).Exec(context.Background())
	require.NoError(t, err)
}
