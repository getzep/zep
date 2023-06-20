package memorystore

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestPutSession(t *testing.T) {
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	tests := []struct {
		name       string
		sessionID  string
		metadata   map[string]interface{}
		wantErr    bool
		errMessage string
	}{
		{
			name:      "Valid session",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"key": "value",
			},
			wantErr: false,
		},
		{
			name:      "duplicate session id should upsert",
			sessionID: sessionID,
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
			result, err := putSession(testCtx, testDB, tt.sessionID, tt.metadata, true)

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
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	metadata := map[string]interface{}{
		"key": "value",
	}
	_, err = putSession(testCtx, testDB, sessionID, metadata, true)
	assert.NoError(t, err)

	tests := []struct {
		name          string
		sessionID     string
		expectedFound bool
	}{
		{
			name:          "Existing session",
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
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	_, err = putSession(testCtx, testDB, sessionID, map[string]interface{}{}, false)
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

func TestPutSessionMetadata(t *testing.T) {
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	tests := []struct {
		name             string
		sessionID        string
		metadata         map[string]interface{}
		expectedError    error
		expectedMetadata map[string]interface{}
	}{
		{
			name:             "Update empty metadata",
			sessionID:        sessionID,
			metadata:         map[string]interface{}{},
			expectedMetadata: map[string]interface{}(nil),
		},
		{
			name:      "Update metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
			},
			expectedMetadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
			},
		},
	}

	ctx := context.Background()
	_, err = putSession(ctx, testDB, sessionID, map[string]interface{}{}, false)
	assert.NoError(t, err, "putSession should not return an error")

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			storedSession, err := putSessionMetadata(ctx, testDB, tt.sessionID, tt.metadata)

			if tt.expectedError != nil {
				assert.Error(t, err)
				assert.Equal(t, tt.expectedError, err)
			} else {
				assert.NoError(t, err)

				// Compare the expected metadata and stored metadata
				assert.Equal(t, tt.expectedMetadata, storedSession.Metadata)
			}
		})
	}
}
