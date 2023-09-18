package postgres

import (
	"context"
	"testing"

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
				assert.NotEmpty(t, result.ID)
				assert.False(t, result.CreatedAt.IsZero())
				assert.Equal(t, tt.session.SessionID, result.SessionID)
				assert.Equal(t, tt.session.Metadata, result.Metadata)
				assert.Equal(t, tt.session.UserID, result.UserID)
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
				assert.Equal(t, session.UserID, result.UserID)
			} else {
				assert.ErrorIs(t, err, models.ErrNotFound)
				assert.Nil(t, result)
			}
		})
	}
}

func TestSessionDAO_Update(t *testing.T) {
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
	createdSession, err := dao.Create(testCtx, session)
	assert.NoError(t, err)

	// Update the session
	updateSession := &models.UpdateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "new value",
		},
	}
	updatedSession, err := dao.Update(testCtx, updateSession, false)
	assert.NoError(t, err)

	// Verify the update
	assert.Equal(t, createdSession.UUID, updatedSession.UUID)
	assert.Equal(t, createdSession.ID, updatedSession.ID)
	assert.Equal(t, createdSession.SessionID, updatedSession.SessionID)
	assert.Equal(t, createdSession.UserID, updatedSession.UserID)
	assert.Equal(t, updateSession.Metadata, updatedSession.Metadata)
	assert.Less(t, createdSession.UpdatedAt, updatedSession.UpdatedAt)
}

func TestSessionDAO_UpdateWithNilMetadata(t *testing.T) {
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
	createdSession, err := dao.Create(testCtx, session)
	assert.NoError(t, err)

	// Update the session
	updateSession := &models.UpdateSessionRequest{
		SessionID: sessionID,
	}
	updatedSession, err := dao.Update(testCtx, updateSession, false)
	assert.NoError(t, err)

	// Verify the update hasn't nilled out the metadata
	assert.Equal(t, createdSession.UUID, updatedSession.UUID)
	assert.Equal(t, createdSession.ID, updatedSession.ID)
	assert.Equal(t, createdSession.SessionID, updatedSession.SessionID)
	assert.Equal(t, createdSession.UserID, updatedSession.UserID)
	assert.Equal(t, session.Metadata, updatedSession.Metadata) // Metadata should not be nil
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

	session := &models.UpdateSessionRequest{
		SessionID: sessionID,
	}
	updatesSession, err := sessionStore.Update(testCtx, session, false)
	assert.NoError(t, err, "Update should not return an error")

	assert.NoError(t, err, "Get should not return an error")
	assert.NotNil(t, updatesSession, "Update should return a session")
	assert.Emptyf(t, updatesSession.DeletedAt, "Update should not have a DeletedAt value")

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

func createTestSessions(t *testing.T, dao *SessionDAO, numSessions int) []*models.Session {
	var sessions []*models.Session
	for i := 0; i < numSessions; i++ {
		sessionID, err := testutils.GenerateRandomSessionID(16)
		assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

		session := &models.CreateSessionRequest{
			SessionID: sessionID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
		}
		createdSession, err := dao.Create(testCtx, session)
		assert.NoError(t, err)

		sessions = append(sessions, createdSession)
	}
	return sessions
}

func TestSessionDAO_ListAll(t *testing.T) {
	CleanDB(t, testDB)
	err := CreateSchema(testCtx, appState, testDB)
	assert.NoError(t, err)

	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	// Create a few test sessions
	sessions := createTestSessions(t, dao, 5)
	lastID := sessions[len(sessions)-1].ID

	tests := []struct {
		name   string
		cursor int64
		limit  int
		want   int
	}{
		{
			name:   "Get all sessions",
			cursor: 0, // start from the beginning
			limit:  10,
			want:   5,
		},
		{
			name:   "Get no sessions",
			cursor: lastID, // start from the last session
			limit:  10,
			want:   0,
		},
		{
			name:   "Limit number of sessions",
			cursor: 0, // start from the beginning
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

func TestSessionDAO_ListAllOrdered(t *testing.T) {
	// Initialize SessionDAO
	CleanDB(t, testDB)
	err := CreateSchema(testCtx, appState, testDB)
	assert.NoError(t, err)

	dao := NewSessionDAO(testDB)

	totalCount := 5
	pageSize := 5

	// Create a few test sessions
	sessions := createTestSessions(t, dao, totalCount)

	tests := []struct {
		name       string
		pageNumber int
		pageSize   int
		orderBy    string
		asc        bool
		want       *models.SessionListResponse
	}{
		{
			name:       "Order by ID ASC",
			pageNumber: 0,
			pageSize:   pageSize,
			orderBy:    "id",
			asc:        true,
			want: &models.SessionListResponse{
				Sessions:   sessions,
				TotalCount: totalCount,
				RowCount:   pageSize,
			},
		},
		{
			name:       "Order by ID DESC",
			pageNumber: 0,
			pageSize:   pageSize,
			orderBy:    "id",
			asc:        false,
			want: &models.SessionListResponse{
				Sessions:   reverse(sessions),
				TotalCount: pageSize,
				RowCount:   pageSize,
			},
		},
		{
			name:       "Order by CreatedAt ASC",
			pageNumber: 0,
			pageSize:   pageSize,
			orderBy:    "created_at",
			asc:        true,
			want: &models.SessionListResponse{
				Sessions:   sessions,
				TotalCount: pageSize,
				RowCount:   pageSize,
			},
		},
		{
			name:       "Order by CreatedAt DESC",
			pageNumber: 0,
			pageSize:   pageSize,
			orderBy:    "created_at",
			asc:        false,
			want: &models.SessionListResponse{
				Sessions:   reverse(sessions),
				TotalCount: pageSize,
				RowCount:   pageSize,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := dao.ListAllOrdered(
				testCtx,
				tt.pageNumber,
				tt.pageSize,
				tt.orderBy,
				tt.asc,
			)
			assert.NoError(t, err)
			assert.Equal(t, tt.want, result)
		})
	}
}

// Helper function to reverse a slice of sessions
func reverse(sessions []*models.Session) []*models.Session {
	reversed := make([]*models.Session, len(sessions))
	for i, session := range sessions {
		reversed[len(sessions)-1-i] = session
	}
	return reversed
}
