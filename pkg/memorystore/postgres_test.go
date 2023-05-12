package memorystore

import (
	"fmt"
	"math/rand"
	"os"
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
	// Set log level to Debug for all tests in this package

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

	cfg, err := test.NewTestConfig()
	if err != nil {
		panic(err)
	}

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
	resultMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
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
	respMessages, err := getMessages(testCtx, testDB, sessionID, memoryWindow, 10)
	assert.NoError(t, err, "getMessages should not return an error")
	assert.Nil(t, respMessages, "getMessages should return nil")

	// Test that summary is deleted
	respSummary, err := getSummary(testCtx, testDB, sessionID)
	assert.NoError(t, err, "getSummary should not return an error")
	assert.Nil(t, respSummary, "getSummary should return nil")
}

func TestPutMessages(t *testing.T) {
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

	// Force embedding to be enabled
	viper.Set("extractor.embeddings.enabled", true)

	t.Run("insert messages", func(t *testing.T) {
		resultMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
		assert.NoError(t, err, "putMessages should not return an error")

		// Verify the inserted messages in the database
		verifyMessagesInDB(t, testCtx, testDB, sessionID, messages, resultMessages)
	})

	t.Run("upsert messages with updated TokenCount", func(t *testing.T) {
		// get messages with UUIDs
		messages, err := getMessages(testCtx, testDB, sessionID, 10, 0)
		assert.NoError(t, err, "putMessages should not return an error")
		// Update TokenCount values for the returned messages
		for i := range messages {
			messages[i].TokenCount = i + 1
		}

		// Call putMessages function to upsert the messages
		resultMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
		assert.NoError(t, err, "putMessages should not return an error")

		// Verify the upserted messages in the database
		verifyMessagesInDB(t, testCtx, testDB, sessionID, messages, resultMessages)
	})
}

func verifyMessagesInDB(
	t *testing.T,
	testCtx context.Context,
	testDB *bun.DB,
	sessionID string,
	expectedMessages,
	resultMessages []models.Message,
) {
	var pgMessages []PgMessageStore
	err := testDB.NewSelect().
		Model(&pgMessages).
		Where("session_id = ?", sessionID).
		Order("created_at ASC").
		Scan(testCtx)
	assert.NoError(t, err, "Database query should not return an error")

	assert.Equal(
		t,
		len(expectedMessages),
		len(pgMessages),
		"Expected number of messages to be equal",
	)
	for i, pgMsg := range pgMessages {
		assert.Equal(t, sessionID, pgMsg.SessionID, "Expected sessionID to be equal")
		assert.Equal(
			t,
			expectedMessages[i].Role,
			pgMsg.Role,
			"Expected message role to be equal",
		)
		assert.Equal(
			t,
			expectedMessages[i].Content,
			pgMsg.Content,
			"Expected message content to be equal",
		)
	}

	assert.Equal(
		t,
		len(expectedMessages),
		len(resultMessages),
		"Expected number of messages to be equal",
	)
	for i, msg := range resultMessages {
		assert.NotEmpty(t, msg.UUID)
		assert.False(t, msg.CreatedAt.IsZero())
		assert.Equal(t, expectedMessages[i].Role, msg.Role, "Expected message role to be equal")
		assert.Equal(
			t,
			expectedMessages[i].Content,
			msg.Content,
			"Expected message content to be equal",
		)
		assert.Equal(t, expectedMessages[i].Metadata, msg.Metadata, "Expected metadata to be equal")
		assert.Equal(
			t,
			expectedMessages[i].TokenCount,
			msg.TokenCount,
			"Expected TokenCount to be equal",
		)
	}
}

func TestGetMessages(t *testing.T) {
	// Create a test session
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(testCtx, testDB, sessionID, metadata)
	assert.NoError(t, err)

	messages, err := putMessages(testCtx, testDB, sessionID, true, test.TestMessages)
	assert.NoError(t, err)

	// Explicitly set the message window to 10
	messageWindow := 10
	summaryPointIndex := len(messages) - 9
	viper.Set("memory.message_window", messageWindow)

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
			expectedLength: messageWindow,
			withSummary:    false,
		},
		{
			name:           "Get all messages up to SummaryPoint",
			sessionID:      sessionID,
			lastNMessages:  0,
			expectedLength: 8,
			withSummary:    true,
		},
		{
			name:           "Get last message",
			sessionID:      sessionID,
			lastNMessages:  1,
			expectedLength: 1,
			withSummary:    false,
		},
		{
			name:           "Non-existent session",
			sessionID:      "nonexistent",
			lastNMessages:  -1,
			expectedLength: 0,
			withSummary:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.withSummary {
				// Create a summary using the test messages. The SummaryPointUUID should be at messageWindow - 2
				_, err = putSummary(
					testCtx,
					testDB,
					sessionID,
					&models.Summary{Content: "Test summary",
						SummaryPointUUID: messages[summaryPointIndex].UUID},
				)
				assert.NoError(t, err)
			}
			result, err := getMessages(
				testCtx,
				testDB,
				tt.sessionID,
				messageWindow,
				tt.lastNMessages,
			)
			assert.NoError(t, err)

			if tt.expectedLength > 0 {
				assert.NotNil(t, result)
				assert.Equal(t, tt.expectedLength, len(result))
				for i, msg := range result {
					assert.NotEmpty(t, msg.UUID)
					assert.False(t, msg.CreatedAt.IsZero())
					assert.Equal(t, test.TestMessages[len(messages)-i-1].Role, msg.Role)
					assert.Equal(t, test.TestMessages[len(messages)-i-1].Content, msg.Content)
					assert.Equal(t, test.TestMessages[len(messages)-i-1].Metadata, msg.Metadata)
				}
			} else {
				assert.Empty(t, result)
			}
		})
	}
}

func TestGetMessageVectorsWhereIsEmbeddedFalse(t *testing.T) {
	// Create a test session
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(testCtx, testDB, sessionID, metadata)
	assert.NoError(t, err)

	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
		{
			Role:     "bot",
			Content:  "Hi there!",
			Metadata: map[string]interface{}{"something": "good"},
		},
	}

	addedMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
	assert.NoError(t, err)

	// getMessageVectors only for isEmbedded = false
	embeddings, err := getMessageVectors(testCtx, testDB, sessionID, false)
	assert.NoError(t, err)
	assert.Equal(t, len(messages), len(embeddings))

	for i, emb := range embeddings {
		assert.NotNil(t, emb.TextUUID)
		assert.NotEmpty(t, emb.Embedding)
		assert.Equal(t, addedMessages[i].UUID, emb.TextUUID)
	}
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
	resultMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
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
	resultMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
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
	resultMessages, err := putMessages(testCtx, testDB, sessionID, true, messages)
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

	err = putEmbeddings(testCtx, testDB, sessionID, embeddings, true)
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

func TestLastSummaryPointIndex(t *testing.T) {
	// CleanDB and setup so expectedSummaryPointIndex is 3
	CleanDB(t, testDB)
	err := ensurePostgresSetup(testCtx, testDB)
	assert.NoError(t, err, "ensurePostgresSetup should not return an error")

	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(testCtx, testDB, sessionID, map[string]interface{}{})
	assert.NoError(t, err, "putSession should not return an error")

	// Call putMessages function using internal.TestMessages
	resultMessages, err := putMessages(testCtx, testDB, sessionID, true, test.TestMessages)
	assert.NoError(t, err, "putMessages should not return an error")

	configuredMessageWindow := 30
	expectedSummaryPointIndex := 3

	tests := []struct {
		name                    string
		sessionID               string
		summaryPointUUID        uuid.UUID
		configuredMessageWindow int
		wantErr                 bool
		errMessage              string
	}{
		{
			name:                    "Valid summary point",
			sessionID:               sessionID,
			summaryPointUUID:        resultMessages[expectedSummaryPointIndex-1].UUID,
			configuredMessageWindow: configuredMessageWindow,
			wantErr:                 false,
		},
		{
			name:                    "Invalid summary point",
			sessionID:               sessionID,
			summaryPointUUID:        uuid.New(),
			configuredMessageWindow: configuredMessageWindow,
			wantErr:                 true,
			errMessage:              "not found",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resultIndex, err := lastSummaryPointIndex(
				testCtx,
				testDB,
				tt.sessionID,
				tt.summaryPointUUID,
				tt.configuredMessageWindow,
			)

			if tt.wantErr {
				assert.Error(t, err)
				storageErr, ok := err.(*StorageError)
				if ok {
					assert.Contains(t, storageErr.message, tt.errMessage)
				}
			} else {
				assert.NoError(t, err)
				assert.NotNil(t, resultIndex)
				assert.Equal(t, int64(expectedSummaryPointIndex), resultIndex)
			}
		})
	}
}

func TestSearch(t *testing.T) {
	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	// Call putMessages function
	msgs, err := putMessages(testCtx, testDB, sessionID, true, test.TestMessages)
	assert.NoError(t, err, "putMessages should not return an error")

	appState.MemoryStore.NotifyExtractors(
		context.Background(),
		appState,
		&models.MessageEvent{SessionID: sessionID,
			Messages: msgs},
	)

	// enrichment runs async. Wait for it to finish
	// This is hacky but I'd prefer not to add a WaitGroup to the putMessages function just for testing purposes
	time.Sleep(time.Second * 2)

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
