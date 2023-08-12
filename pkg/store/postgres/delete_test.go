package postgres

import (
	"context"
	"testing"

	"github.com/uptrace/bun"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func setupTestDeleteData(ctx context.Context, testDB *bun.DB) (string, error) {
	// Test data
	sessionID, err := testutils.GenerateRandomSessionID(16)
	if err != nil {
		return "", err
	}

	_, err = putSession(ctx, testDB, sessionID, nil, true)
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

func TestDeleteSession(t *testing.T) {
	memoryWindow := 10
	appState.Config.Memory.MessageWindow = memoryWindow

	sessionID, err := setupTestDeleteData(testCtx, testDB)
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

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

func TestUndeleteSession(t *testing.T) {
	sessionID, err := setupTestDeleteData(testCtx, testDB)
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

	err = deleteSession(testCtx, testDB, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	s, err := putSession(testCtx, testDB, sessionID, nil, false)
	assert.NoError(t, err, "putSession should not return an error")
	assert.NotNil(t, s, "putSession should return a session")
	assert.Emptyf(t, s.DeletedAt, "putSession should not have a DeletedAt value")

	// Test that messages remain deleted
	respMessages, err := getMessages(testCtx, testDB, sessionID, 2, nil, 0)
	assert.NoError(t, err, "getMessages should not return an error")
	assert.Nil(t, respMessages, "getMessages should return nil")
}

func TestPurgeDeleted(t *testing.T) {
	sessionID, err := setupTestDeleteData(testCtx, testDB)
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

	err = deleteSession(testCtx, testDB, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	err = purgeDeleted(testCtx, testDB)
	assert.NoError(t, err, "purgeDeleted should not return an error")

	// Test that session is deleted
	for _, schema := range messageTableList {
		r, err := testDB.NewSelect().
			Model(schema).
			WhereDeleted().
			Exec(testCtx)
		assert.NoError(t, err, "purgeDeleted should not return an error")
		rows, err := r.RowsAffected()
		assert.NoError(t, err, "RowsAffected should not return an error")
		assert.True(t, rows == 0, "purgeDeleted should Delete all rows")
	}
}
