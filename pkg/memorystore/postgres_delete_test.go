package memorystore

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

	_, err = putSession(ctx, testDB, sessionID, map[string]interface{}{})
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

func TestPurgeDeleted(t *testing.T) {
	sessionID, err := setupTestDeleteData(testCtx, testDB)
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

	err = deleteSession(testCtx, testDB, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	err = purgeDeleted(testCtx, testDB)
	assert.NoError(t, err, "purgeDeleted should not return an error")

	// Test that session is deleted
	for _, schema := range tableList {
		r, err := testDB.NewSelect().
			Model(schema).
			WhereDeleted().
			Exec(testCtx)
		assert.NoError(t, err, "purgeDeleted should not return an error")
		rows, err := r.RowsAffected()
		assert.NoError(t, err, "RowsAffected should not return an error")
		assert.True(t, rows == 0, "purgeDeleted should delete all rows")
	}
}
