package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"

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

	collection := &models.DocumentCollection{
		UUID:                uuid.New(),
		Name:                "Test",
		EmbeddingDimensions: 3,
	}

	_, err := createDocumentTable(ctx, testDB, collection)
	if err != nil {
		t.Fatalf("failed to create document table: %v", err)
	}
}
