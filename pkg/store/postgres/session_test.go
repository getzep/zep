package postgres

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
	"github.com/uptrace/bun"
)

func TestSessionDAO_Create(t *testing.T) {
	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	tests := []struct {
		name       string
		session    *models.CreateSessionRequest
		wantErr    bool
		errMessage string
	}{
		{
			name: "Valid session",
			session: &models.CreateSessionRequest{
				SessionID: sessionID,
				Metadata: map[string]interface{}{
					"key": "value",
				}},
			wantErr: false,
		},
		{
			name: "Empty session ID",
			session: &models.CreateSessionRequest{
				SessionID: "",
				Metadata: map[string]interface{}{
					"key": "value",
				}},
			wantErr:    true,
			errMessage: "sessionID cannot be empty",
		},
		// Add more test cases as needed
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := dao.Create(testCtx, tt.session)

			if tt.wantErr {
				assert.Error(t, err)
				assert.Equal(t, tt.errMessage, err.Error())
			} else {
				assert.NoError(t, err)
				assert.NotNil(t, result)
				assert.NotEmpty(t, result.UUID)
				assert.False(t, result.CreatedAt.IsZero())
				assert.Equal(t, tt.session.SessionID, result.SessionID)
				assert.Equal(t, tt.session.Metadata, result.Metadata)
				assert.Equal(t, tt.session.UserUUID, result.UserUUID)
			}
		})
	}
}

func TestSessionDAO_Get(t *testing.T) {
	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	// Create a test session
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}
	_, err = dao.Create(testCtx, session)
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
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := dao.Get(testCtx, tt.sessionID)

			if tt.expectedFound {
				assert.NoError(t, err)
				assert.NotNil(t, result)
				assert.NotEmpty(t, result.UUID)
				assert.False(t, result.CreatedAt.IsZero())
				assert.Equal(t, tt.sessionID, result.SessionID)
				assert.Equal(t, session.Metadata, result.Metadata)
				assert.Equal(t, session.UserUUID, result.UserUUID)
			} else {
				assert.ErrorIs(t, err, models.ErrNotFound)
				assert.Nil(t, result)
			}
		})
	}
}

func TestSessionDAO_Delete(t *testing.T) {
	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	// Create a test session
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}
	_, err = dao.Create(testCtx, session)
	assert.NoError(t, err)

	tests := []struct {
		name          string
		sessionID     string
		expectedError error
	}{
		{
			name:          "Existing session",
			sessionID:     sessionID,
			expectedError: nil,
		},
		{
			name:          "Non-existent session",
			sessionID:     "nonexistent",
			expectedError: models.ErrNotFound,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := dao.Delete(testCtx, tt.sessionID)

			if tt.expectedError != nil {
				assert.Error(t, err)
				assert.ErrorIs(t, err, tt.expectedError)
			} else {
				assert.NoError(t, err)

				// Verify the session is deleted
				_, err := dao.Get(testCtx, tt.sessionID)
				assert.ErrorIs(t, err, models.ErrNotFound)
			}
		})
	}
}

func TestSessionDAO_mergeSessionMetadata(t *testing.T) {
	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	// Create a test session
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"A": 1,
			"B": map[string]interface{}{
				"C": 2,
			},
			"Z": 3,
		},
	}
	_, err = dao.Create(testCtx, session)
	assert.NoError(t, err)

	tests := []struct {
		name             string
		sessionID        string
		metadata         map[string]interface{}
		privileged       bool
		expectedError    error
		expectedMetadata map[string]interface{}
	}{
		{
			name:      "Update metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 3, // Should override initial value of "A"
				"B": map[string]interface{}{
					"D": 4, // Should be added to map under "B"
					"E": map[string]interface{}{
						"F": 5, // Test deeply nested map
					},
				},
			},
			privileged: false,
			expectedMetadata: map[string]interface{}{
				"A": 3, // Updated value
				"B": map[string]interface{}{
					"C": json.Number("2"), // Initial value will be converted to json.Number
					"D": 4,                // New value
					"E": map[string]interface{}{
						"F": 5, // New value from deeply nested map
					},
				},
				"Z": json.Number("3"), // Initial value will be converted to json.Number
			},
		},
		{
			name:      "Unprivileged update with system metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"system": map[string]interface{}{
					"foo": "bar", // This should be ignored
				},
			},
			privileged: false,
			expectedMetadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"Z": json.Number("3"), // Initial value will be converted to json.Number
			},
		},
		{
			name:      "Privileged update with system metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"system": map[string]interface{}{
					"foo": "bar", // This should NOT be ignored
				},
			},
			privileged: true,
			expectedMetadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"Z": json.Number("3"), // Initial value will be converted to json.Number
				"system": map[string]interface{}{
					"foo": "bar",
				},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mergedMetadata, err := mergeSessionMetadata(
				testCtx,
				testDB,
				tt.sessionID,
				tt.metadata,
				tt.privileged,
			)

			if tt.expectedError != nil {
				assert.Error(t, err)
				assert.Equal(t, tt.expectedError, err)
			} else {
				assert.NoError(t, err)

				// Compare the expected metadata and merged metadata
				assert.Equal(t, tt.expectedMetadata, mergedMetadata)
			}
		})
	}
}

func TestSessionDAO_DeleteSessionDeletesSummaryMessages(t *testing.T) {
	memoryWindow := 10
	appState.Config.Memory.MessageWindow = memoryWindow

	sessionStore := NewSessionDAO(testDB)

	sessionID, err := setupTestDeleteData(testCtx, testDB)
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

	err = sessionStore.Delete(testCtx, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	// Test that session is deleted
	_, err = sessionStore.Get(testCtx, sessionID)
	assert.ErrorIs(t, err, models.ErrNotFound)

	// Test that messages are deleted
	respMessages, err := getMessages(testCtx, testDB, sessionID, memoryWindow, nil, 0)
	assert.NoError(t, err, "getMessages should not return an error")
	assert.Nil(t, respMessages, "getMessages should return nil")

	// Test that summary is deleted
	respSummary, err := getSummary(testCtx, testDB, sessionID)
	assert.NoError(t, err, "getSummary should not return an error")
	assert.Nil(t, respSummary, "getSummary should return nil")
}

func TestSessionDAO_UndeleteSession(t *testing.T) {
	sessionID, err := setupTestDeleteData(testCtx, testDB)
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

	sessionStore := NewSessionDAO(testDB)

	err = sessionStore.Delete(testCtx, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	session := &models.SessionUpdateRequest{
		SessionID: sessionID,
	}
	err = sessionStore.Update(testCtx, session, false)
	assert.NoError(t, err, "Update should not return an error")

	s, err := sessionStore.Get(testCtx, sessionID)
	assert.NoError(t, err, "Get should not return an error")
	assert.NotNil(t, s, "Update should return a session")
	assert.Emptyf(t, s.DeletedAt, "Update should not have a DeletedAt value")

	// Test that messages remain deleted
	respMessages, err := getMessages(testCtx, testDB, sessionID, 2, nil, 0)
	assert.NoError(t, err, "getMessages should not return an error")
	assert.Nil(t, respMessages, "getMessages should return nil")
}

func setupTestDeleteData(ctx context.Context, testDB *bun.DB) (string, error) {
	// Test data
	sessionID, err := testutils.GenerateRandomSessionID(16)
	if err != nil {
		return "", err
	}

	dao := NewSessionDAO(testDB)
	_, err = dao.Create(ctx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	if err != nil {
		return "", err
	}

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
	resultMessages, err := putMessages(ctx, testDB, sessionID, messages)
	if err != nil {
		return "", err
	}

	summary := models.Summary{
		Content: "This is a summary",
		Metadata: map[string]interface{}{
			"timestamp": 1629462551,
		},
		SummaryPointUUID: resultMessages[0].UUID,
	}
	_, err = putSummary(ctx, testDB, sessionID, &summary)
	if err != nil {
		return "", err
	}

	return sessionID, nil
}

func TestSessionDAO_ListAll(t *testing.T) {
	CleanDB(t, testDB)
	err := ensurePostgresSetup(testCtx, appState, testDB)
	assert.NoError(t, err)

	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	// Create a few test sessions
	for i := 0; i < 5; i++ {
		sessionID, err := testutils.GenerateRandomSessionID(16)
		assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

		session := &models.CreateSessionRequest{
			SessionID: sessionID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
		}
		_, err = dao.Create(testCtx, session)
		assert.NoError(t, err)

		// Update the CreatedAt field to a time relative to now
		_, err = dao.db.NewUpdate().
			Model(&SessionSchema{}).
			Set("created_at = ?", time.Now().Add(-time.Duration(i)*time.Hour)).
			Where("session_id = ?", sessionID).
			Exec(testCtx)
		assert.NoError(t, err)
	}

	tests := []struct {
		name   string
		cursor time.Time
		limit  int
		want   int
	}{
		{
			name:   "Get all sessions",
			cursor: time.Now().Add(-5 * time.Hour), // 5 hours ago
			limit:  10,
			want:   5,
		},
		{
			name:   "Get sessions last 2 hours",
			cursor: time.Now().Add(-2 * time.Hour), // 2 hours ago
			limit:  10,
			want:   2,
		},
		{
			name:   "Get no sessions",
			cursor: time.Now().Add(time.Hour), // 1 hour in the future
			limit:  10,
			want:   0,
		},
		{
			name:   "Limit number of sessions",
			cursor: time.Now().Add(-5 * time.Hour), // all sessions
			limit:  3,
			want:   3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sessions, err := dao.ListAll(testCtx, tt.cursor, tt.limit)
			assert.NoError(t, err)
			assert.Equal(t, tt.want, len(sessions))
		})
	}
}
