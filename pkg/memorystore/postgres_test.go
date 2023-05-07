package memorystore

import (
	"fmt"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/danielchalef/zep/test"
	"github.com/google/uuid"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/uptrace/bun"
	"math/rand"
	"testing"
	"time"

	"context"
)

// TODO: Add context deadlines to all tests
func TestEnsurePostgresSchemaSetup(t *testing.T) {
	ctx := context.Background()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	t.Run("should succeed when all schema setup is successful", func(t *testing.T) {
		err := ensurePostgresSetup(ctx, db)
		assert.NoError(t, err)

		checkForTable(t, db, &PgSession{})
		checkForTable(t, db, &PgMessageStore{})
		checkForTable(t, db, &PgSummaryStore{})
		checkForTable(t, db, &PgMessageVectorStore{})
	})
	t.Run("should not fail on second run", func(t *testing.T) {
		err := ensurePostgresSetup(ctx, db)
		assert.NoError(t, err)
	})
}

func TestPutSession(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

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
			result, err := putSession(ctx, db, tt.sessionID, tt.metadata)

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
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Create a test session
	sessionID := "123abc"
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(ctx, db, sessionID, metadata)
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
			result, err := getSession(ctx, db, tt.sessionID)
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
	viper.Set("memory.message_window", memoryWindow)

	ctx := context.Background()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(ctx, db, sessionID, map[string]interface{}{})
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
	resultMessages, err := putMessages(ctx, db, sessionID, true, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	// Put a summary
	summary := models.Summary{
		Content: "This is a summary",
		Metadata: map[string]interface{}{
			"timestamp": 1629462551,
		},
		SummaryPointUUID: resultMessages[0].UUID,
	}
	_, err = putSummary(ctx, db, sessionID, &summary)
	assert.NoError(t, err, "putSummary should not return an error")

	err = deleteSession(ctx, db, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	// Test that session is deleted
	resp, err := getSession(ctx, db, sessionID)
	assert.NoError(t, err, "getSession should not return an error")
	assert.Nil(t, resp, "getSession should return nil")

	// Test that messages are deleted
	respMessages, err := getMessages(ctx, db, sessionID, memoryWindow, 10)
	assert.NoError(t, err, "getMessages should not return an error")
	assert.Nil(t, respMessages, "getMessages should return nil")

	// Test that summary is deleted
	respSummary, err := getSummary(ctx, db, sessionID)
	assert.NoError(t, err, "getSummary should not return an error")
	assert.Nil(t, respSummary, "getSummary should return nil")
}

func TestPutMessages(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(ctx, db, sessionID, map[string]interface{}{})
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

	// Call putMessages function
	resultMessages, err := putMessages(ctx, db, sessionID, true, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	// Query the database and verify the inserted messages
	var pgMessages []PgMessageStore
	err = db.NewSelect().
		Model(&pgMessages).
		Where("session_id = ?", sessionID).
		Order("created_at ASC").
		Scan(ctx)
	assert.NoError(t, err, "Database query should not return an error")

	assert.Equal(t, len(messages), len(pgMessages), "Expected number of messages to be equal")
	for i, pgMsg := range pgMessages {
		assert.Equal(t, sessionID, pgMsg.SessionID, "Expected sessionID to be equal")
		assert.Equal(
			t,
			messages[i].Role,
			pgMsg.Role,
			"Expected message role to be equal",
		)
		assert.Equal(
			t,
			messages[i].Content,
			pgMsg.Content,
			"Expected message content to be equal",
		)
	}

	assert.Equal(t, len(messages), len(resultMessages), "Expected number of messages to be equal")
	for i, msg := range resultMessages {
		assert.NotEmpty(t, msg.UUID)
		assert.False(t, msg.CreatedAt.IsZero())
		assert.Equal(t, messages[i].Role, msg.Role, "Expected message role to be equal")
		assert.Equal(t, messages[i].Content, msg.Content, "Expected message content to be equal")
		assert.Equal(t, messages[i].Metadata, msg.Metadata, "Expected metadata to be equal")
	}

	// Check for the creation of PgMessageVectorStore values
	var pgMemoryVectorStores []PgMessageVectorStore
	err = db.NewSelect().
		Model(&pgMemoryVectorStores).
		Where("session_id = ?", sessionID).
		Order("uuid ASC").
		Scan(ctx)
	assert.NoError(t, err, "Database query should not return an error")

	assert.Equal(
		t,
		len(messages),
		len(pgMemoryVectorStores),
		"Expected number of memory vector records to be equal",
	)
	for _, pgMemoryVectorStore := range pgMemoryVectorStores {
		assert.Equal(t, sessionID, pgMemoryVectorStore.SessionID, "Expected sessionID to be equal")
		assert.Equal(t, false, pgMemoryVectorStore.IsEmbedded, "Expected IsEmbedded to be false")
	}
}

func TestGetMessages(t *testing.T) {
	ctx := context.Background()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Create a test session
	sessionID := "123abc"
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(ctx, db, sessionID, metadata)
	assert.NoError(t, err)

	messages, err := putMessages(ctx, db, sessionID, true, test.TestMessages)
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
			sessionID:      "123abc",
			lastNMessages:  0,
			expectedLength: messageWindow,
			withSummary:    false,
		},
		{
			name:           "Get all messages up to SummaryPoint",
			sessionID:      "123abc",
			lastNMessages:  0,
			expectedLength: 8,
			withSummary:    true,
		},
		{
			name:           "Get last message",
			sessionID:      "123abc",
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
				_, err = putSummary(ctx, db, sessionID, &models.Summary{Content: "Test summary",
					SummaryPointUUID: messages[summaryPointIndex].UUID})
				assert.NoError(t, err)
			}
			result, err := getMessages(ctx, db, tt.sessionID, messageWindow, tt.lastNMessages)
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
	// Force embedding to be enabled
	viper.Set("extractor.embeddings.enabled", true)

	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Create a test session
	sessionID := "123abc"
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(ctx, db, sessionID, metadata)
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

	addedMessages, err := putMessages(ctx, db, sessionID, true, messages)
	assert.NoError(t, err)

	// getMessageVectors only for isEmbedded = false
	embeddings, err := getMessageVectors(ctx, db, sessionID, false)
	assert.NoError(t, err)
	assert.Equal(t, len(messages), len(embeddings))

	for i, emb := range embeddings {
		assert.NotNil(t, emb.TextUUID)
		assert.NotEmpty(t, emb.Embedding)
		assert.Equal(t, addedMessages[i].UUID, emb.TextUUID)
	}
}

func TestPutSummary(t *testing.T) {
	ctx := context.Background()
	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(ctx, db, sessionID, map[string]interface{}{})
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
	resultMessages, err := putMessages(ctx, db, sessionID, true, messages)
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
				ctx,
				db,
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
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Create a test session
	sessionID := "123abc"
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(ctx, db, sessionID, metadata)
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
	resultMessages, err := putMessages(ctx, db, sessionID, true, messages)
	assert.NoError(t, err, "putMessages should not return an error")

	summary.SummaryPointUUID = resultMessages[0].UUID
	_, err = putSummary(ctx, db, sessionID, &summary)
	assert.NoError(t, err, "putSummary should not return an error")

	summaryTwo.SummaryPointUUID = resultMessages[1].UUID
	putSummaryResultTwo, err := putSummary(ctx, db, sessionID, &summaryTwo)
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
			result, err := getSummary(ctx, db, tt.sessionID)
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
	viper.Set("memory.message_window", 10)
	ctx := context.Background()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Create a test session
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(ctx, db, sessionID, map[string]interface{}{})
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
	resultMessages, err := putMessages(ctx, db, sessionID, true, messages)
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

	err = putEmbeddings(ctx, db, sessionID, embeddings, true)
	assert.NoError(t, err, "putEmbeddings should not return an error")

	// Check for the creation of PgMessageVectorStore values
	var pgMemoryVectorStores []PgMessageVectorStore
	err = db.NewSelect().
		Model(&pgMemoryVectorStores).
		Where("session_id = ?", sessionID).
		Order("uuid ASC").
		Scan(ctx)
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
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*5)
	defer cancel()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	CleanDB(t, db)

	err := ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(ctx, db, sessionID, map[string]interface{}{})
	assert.NoError(t, err, "putSession should not return an error")

	// Call putMessages function using internal.TestMessages
	resultMessages, err := putMessages(ctx, db, sessionID, true, test.TestMessages)
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
				ctx,
				db,
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
	ctx := context.Background()

	db := NewPostgresConn(test.TestDsn)
	defer db.Close()

	cfg, err := test.NewTestConfig()
	assert.NoError(t, err)

	appState := &models.AppState{}
	appState.OpenAIClient = llms.CreateOpenAIClient(cfg)
	appState.Config = cfg

	CleanDB(t, db)

	err = ensurePostgresSetup(ctx, db)
	assert.NoError(t, err)

	// Test data
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	// Force embedding to be enabled
	viper.Set("extractor.embeddings.enabled", true)

	// Call putMessages function
	_, err = putMessages(ctx, db, sessionID, true, test.TestMessages)
	assert.NoError(t, err, "putMessages should not return an error")

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

			s, err := searchMessages(ctx, appState, db, sessionID, &q, expectedLastN)

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
				}
			}
		})
	}
}

func checkForTable(t *testing.T, db *bun.DB, schema interface{}) {
	_, err := db.NewSelect().Model(schema).Limit(0).Exec(context.Background())
	require.NoError(t, err)
}
