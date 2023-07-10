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

func TestGetCollection(t *testing.T) {
	ctx := context.Background()

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

	testCases := []struct {
		name             string
		collectionName   string
		expectedError    string
		expectedNotFound bool
	}{
		{
			name:           "test get collection",
			collectionName: collection.Name,
			expectedError:  "",
		},
		{
			name:             "test when collection does not exist",
			collectionName:   testutils.GenerateRandomString(10),
			expectedError:    "no rows in result set",
			expectedNotFound: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			coll, err := getCollection(ctx, testDB, tc.collectionName)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				assert.Equal(t, collection.UUID, coll.UUID)
				assert.Equal(t, tc.collectionName, coll.Name)
				assert.Equal(t, collection.EmbeddingDimensions, coll.EmbeddingDimensions)
			}
			if tc.expectedNotFound {
				assert.Nil(t, coll)
			}
		})
	}
}

func TestGetCollectionList(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	var collectionsToCreate []models.DocumentCollection

	for i := 0; i < 3; i++ {
		collection := &models.DocumentCollection{
			UUID:                uuid.New(),
			Name:                testutils.GenerateRandomString(10),
			EmbeddingDimensions: 10,
		}
		err = putCollection(ctx, testDB, collection)
		assert.NoError(t, err)

		collectionsToCreate = append(collectionsToCreate, *collection)
	}

	retrievedCollections, err := getCollectionList(ctx, testDB)
	assert.NoError(t, err)

	// Compare lengths of created and retrieved collections
	assert.Equal(t, len(collectionsToCreate), len(retrievedCollections))

	// For each created collection, check if there is a matching retrieved collection
	for _, createdColl := range collectionsToCreate {
		matched := false
		for _, retrievedColl := range retrievedCollections {
			if createdColl.Name == retrievedColl.Name {
				matched = true
				break
			}
		}
		assert.True(t, matched, "Created collection not found in retrieved collections")
	}
}

func TestDeleteCollection(t *testing.T) {
	ctx := context.Background()

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

	testCases := []struct {
		name                string
		collectionName      string
		expectedErrorString string
	}{
		{
			name:                "test delete existing collection",
			collectionName:      collection.Name,
			expectedErrorString: "",
		},
		{
			name:                "test delete non-existent collection",
			collectionName:      testutils.GenerateRandomString(10),
			expectedErrorString: "no rows in result set",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err = deleteCollection(ctx, testDB, tc.collectionName)

			if tc.expectedErrorString != "" {
				assert.Error(t, err)
				assert.ErrorContains(t, err, tc.expectedErrorString)
			} else {
				assert.NoError(t, err)

				// Try to retrieve the deleted collection
				_, err := getCollection(ctx, testDB, tc.collectionName)
				assert.Error(t, err)
			}
		})
	}
}
