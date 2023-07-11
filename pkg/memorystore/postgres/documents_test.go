package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
)

func TestPutGetDocuments(t *testing.T) {
	ctx := context.Background()

	// Setup
	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := &models.DocumentCollection{
		UUID:                uuid.New(),
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
	}

	err = putCollection(ctx, testDB, collection)
	assert.NoError(t, err)

	// Create documents
	var documents []*models.Document
	for i := 0; i < 5; i++ {
		document := &models.Document{
			Content: testutils.GenerateRandomString(100),
		}
		documents = append(documents, document)
	}

	// Test putDocuments
	err = putDocuments(ctx, testDB, collection.Name, documents)
	assert.NoError(t, err)

	// Validate insertion by retrieving the documents
	// Here it is assumed that you have a corresponding getDocuments() method which retrieves documents
	// from a collection in your DB.
	retrievedDocuments, err := getDocuments(ctx, testDB, collection.Name, 10)
	assert.NoError(t, err)

	// The lengths of the original and retrieved documents should be equal
	assert.Equal(t, len(documents), len(retrievedDocuments))
	for i := 0; i < len(documents); i++ {
		assert.Equal(t, documents[i].Content, retrievedDocuments[i].Content)
	}
}

func TestGetDocument(t *testing.T) {
	ctx := context.Background()

	// Setup
	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := &models.DocumentCollection{
		UUID:                uuid.New(),
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
	}

	err = putCollection(ctx, testDB, collection)
	assert.NoError(t, err)

	document := &models.Document{
		Content: testutils.GenerateRandomString(100),
	}

	documents := []*models.Document{document}
	err = putDocuments(ctx, testDB, collection.Name, documents)
	assert.NoError(t, err)

	testCases := []struct {
		name        string
		collection  string
		uuid        uuid.UUID
		expectError bool
	}{
		{
			name:        "test get existing document",
			collection:  collection.Name,
			uuid:        documents[0].UUID,
			expectError: false,
		},
		{
			name:        "test get non-existent document",
			collection:  collection.Name,
			uuid:        uuid.New(),
			expectError: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			retrievedDoc, err := getDocument(ctx, testDB, tc.collection, tc.uuid)
			if tc.expectError {
				assert.ErrorContains(t, err, "document not found")
			} else {
				assert.NoError(t, err)
				assert.Equal(t, document.Content, retrievedDoc.Content)
			}
		})
	}
}
