package postgres

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestPurgeDeleted(t *testing.T) {
	sessionID, err := setupSessionDeleteTestData(t, testCtx, testDB, "")
	assert.NoError(t, err, "setupTestDeleteData should not return an error")

	sessionStore := NewSessionDAO(testDB)
	err = sessionStore.Delete(testCtx, sessionID)
	assert.NoError(t, err, "deleteSession should not return an error")

	err = purgeDeleted(testCtx, testDB)
	assert.NoError(t, err, "purgeDeleted should not return an error")

	// Test that session is deleted
	for _, schema := range messageTableList {
		r, err := testDB.NewSelect().
			Model(schema).
			WhereDeleted().
			Exec(testCtx)
		assert.NoError(t, err, "NewSelect should not return an error")
		rows, err := r.RowsAffected()
		assert.NoError(t, err, "RowsAffected should not return an error")
		assert.True(t, rows == 0, "purgeDeleted should Delete all rows")
	}
}
