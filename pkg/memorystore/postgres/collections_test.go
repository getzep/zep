package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestPutCollection(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := &models.DocumentCollection{
		UUID:                uuid.New(),
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
	}

	testCases := []struct {
		name          string
		collection    *models.DocumentCollection
		expectedError error
	}{
		{
			name:          "test create collection",
			collection:    collection,
			expectedError: nil,
		},
		{
			name:          "test when collection already exists. should not fail",
			collection:    collection,
			expectedError: nil,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := putCollection(ctx, testDB, tc.collection)
			if tc.expectedError != nil {
				assert.ErrorIs(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
			}
		})
	}
}
