package postgres

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestEnsurePostgresSchemaSetup(t *testing.T) {
	CleanDB(t, testDB)

	t.Run("should succeed when all schema setup is successful", func(t *testing.T) {
		err := ensurePostgresSetup(testCtx, appState, testDB)
		assert.NoError(t, err)

		checkForTable(t, testDB, &SessionSchema{})
		checkForTable(t, testDB, &MessageStoreSchema{})
		checkForTable(t, testDB, &SummaryStoreSchema{})
		checkForTable(t, testDB, &MessageVectorStoreSchema{})
	})
	t.Run("should not fail on second run", func(t *testing.T) {
		err := ensurePostgresSetup(testCtx, appState, testDB)
		assert.NoError(t, err)
	})
}

func TestCreateDocumentTable(t *testing.T) {
	ctx := context.Background()

	collection := NewTestCollectionDAO(3)

	tableName, err := generateDocumentTableName(&collection)
	assert.NoError(t, err)

	err = createDocumentTable(ctx, testDB, tableName, collection.EmbeddingDimensions)
	assert.NoError(t, err)
}
