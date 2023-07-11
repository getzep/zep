package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/google/uuid"

	"github.com/stretchr/testify/assert"
)

func TestCollectionPut(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
	}

	testCases := []struct {
		name          string
		collection    *DocumentCollection
		expectedError error
	}{
		{
			name:          "test create collection",
			collection:    &collection,
			expectedError: nil,
		},
		{
			name:          "test when collection already exists. should not fail",
			collection:    &collection,
			expectedError: nil,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.put(ctx, testDB)
			if tc.expectedError != nil {
				assert.ErrorIs(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
			}
		})
	}
}

func TestCollectionGetByName(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
	}

	err = collection.put(ctx, testDB)
	assert.NoError(t, err)

	testCases := []struct {
		name             string
		collection       DocumentCollection
		expectedError    string
		expectedNotFound bool
	}{
		{
			name:          "test get collection",
			collection:    collection,
			expectedError: "",
		},
		{
			name:             "test when collection does not exist",
			collection:       DocumentCollection{Name: testutils.GenerateRandomString(10)},
			expectedError:    "no rows in result set",
			expectedNotFound: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.getByName(ctx, testDB)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				assert.Equal(t, collection.UUID, tc.collection.UUID)
				assert.Equal(t, collection.EmbeddingDimensions, tc.collection.EmbeddingDimensions)
			}
			if tc.expectedNotFound {
				assert.Equal(t, tc.collection.UUID, uuid.Nil)
			}
		})
	}
}

func TestCollectionGetAll(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	var collectionsToCreate []DocumentCollection

	for i := 0; i < 3; i++ {
		collection := DocumentCollection{
			Name:                testutils.GenerateRandomString(10),
			EmbeddingDimensions: 10,
		}
		err = collection.put(ctx, testDB)
		assert.NoError(t, err)

		collectionsToCreate = append(collectionsToCreate, collection)
	}

	retrievedCollections, err := collectionsToCreate[0].getAll(ctx, testDB)
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

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
	}

	err = collection.put(ctx, testDB)
	assert.NoError(t, err)

	testCases := []struct {
		name                string
		collection          DocumentCollection
		expectedErrorString string
		expectedNotFound    bool
	}{
		{
			name:                "test delete of existing collection",
			collection:          collection,
			expectedErrorString: "",
		},
		{
			name:                "test when collection does not exist",
			collection:          DocumentCollection{Name: testutils.GenerateRandomString(10)},
			expectedErrorString: "no rows in result set",
			expectedNotFound:    true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err = tc.collection.delete(ctx, testDB)

			if tc.expectedErrorString != "" {
				assert.Error(t, err)
				assert.ErrorContains(t, err, tc.expectedErrorString)
			} else {
				assert.NoError(t, err)

				// Try to retrieve the deleted collection
				err := tc.collection.getByName(ctx, testDB)
				assert.Error(t, err)
			}
		})
	}
}

func TestDocumentCollectionPutDocuments(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 5,
	}
	err = collection.put(ctx, testDB)
	assert.NoError(t, err)

	documents := make([]*Document, 10)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				Content: testutils.GenerateRandomString(10),
			},
		}
	}

	testCases := []struct {
		name          string
		collection    DocumentCollection
		documents     []*Document
		expectedError string
	}{
		{
			name:          "test put documents into an existing collection",
			collection:    collection,
			documents:     documents,
			expectedError: "",
		},
		{
			name:          "test put documents into a non-existent collection",
			collection:    DocumentCollection{UUID: uuid.New(), Name: "NonExistentCollection"},
			documents:     documents,
			expectedError: "failed to get collection",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.putDocuments(ctx, testDB, tc.documents)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)

				returnedDocuments, err := tc.collection.getDocuments(ctx, testDB, 0, []*Document{})
				assert.NoError(t, err)

				assert.Equal(t, len(tc.documents), len(returnedDocuments))
				for i := range tc.documents {
					assert.Equal(t, tc.documents[i].Content, returnedDocuments[i].Content)
				}
			}
		})
	}
}

func TestDocumentCollectionGetDocuments(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 5,
	}
	err = collection.put(ctx, testDB)
	assert.NoError(t, err)

	documents := make([]*Document, 10)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				Content: testutils.GenerateRandomString(10),
			},
		}
	}

	err = collection.putDocuments(ctx, testDB, documents)
	assert.NoError(t, err)

	testCases := []struct {
		name          string
		collection    DocumentCollection
		limit         int
		documents     []*Document
		expectedError string
	}{
		{
			name:          "test get all documents",
			collection:    collection,
			limit:         0,
			documents:     nil,
			expectedError: "",
		},
		{
			name:          "test get limited number of documents",
			collection:    collection,
			limit:         5,
			documents:     nil,
			expectedError: "",
		},
		{
			name:          "test get specific documents",
			collection:    collection,
			limit:         0,
			documents:     documents[:5],
			expectedError: "",
		},
		{
			name:          "test get documents from non-existent collection",
			collection:    DocumentCollection{UUID: uuid.New(), Name: "NonExistentCollection"},
			limit:         0,
			documents:     nil,
			expectedError: "failed to get collection",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			returnedDocuments, err := tc.collection.getDocuments(
				ctx,
				testDB,
				tc.limit,
				tc.documents,
			)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				if tc.limit > 0 {
					assert.True(t, len(returnedDocuments) <= tc.limit)
				} else if tc.documents != nil {
					assert.Equal(t, len(tc.documents), len(returnedDocuments))
					for i := range tc.documents {
						assert.Equal(t, tc.documents[i].Content, returnedDocuments[i].Content)
					}
				} else {
					assert.Equal(t, len(documents), len(returnedDocuments))
					for i := range documents {
						assert.Equal(t, documents[i].Content, returnedDocuments[i].Content)
					}
				}
			}
		})
	}
}

func TestDocumentCollectionDeleteDocumentByUUID(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 5,
	}
	err = collection.put(ctx, testDB)
	assert.NoError(t, err)

	document := Document{
		DocumentBase: DocumentBase{
			Content: testutils.GenerateRandomString(10),
		},
	}
	err = collection.putDocuments(ctx, testDB, []*Document{&document})
	assert.NoError(t, err)

	testCases := []struct {
		name          string
		collection    DocumentCollection
		documentUUID  uuid.UUID
		expectedError string
	}{
		{
			name:          "test delete existing document",
			collection:    collection,
			documentUUID:  document.UUID,
			expectedError: "",
		},
		{
			name:          "test delete non-existent document",
			collection:    collection,
			documentUUID:  uuid.New(),
			expectedError: "document not found",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.deleteDocumentByUUID(ctx, testDB, tc.documentUUID)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, "document not found")
			} else {
				assert.NoError(t, err)
				returnedDocuments, err := tc.collection.getDocuments(ctx, testDB, 0, []*Document{})
				assert.NoError(t, err)
				assert.Equal(t, 0, len(returnedDocuments))
			}
		})
	}
}

func TestDocumentCollectionPutDocumentEmbeddings(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 5,
	}
	err = collection.put(ctx, testDB)
	assert.NoError(t, err)

	document := Document{
		DocumentBase: DocumentBase{
			Content: testutils.GenerateRandomString(10),
		},
		Embedding: []float32{0.1, 0.2, 0.3, 0.4, 0.5},
	}
	err = collection.putDocuments(ctx, testDB, []*Document{&document})
	assert.NoError(t, err)

	testCases := []struct {
		name               string
		collection         DocumentCollection
		documentEmbeddings []*Document
		expectedError      string
	}{
		{
			name:       "test update existing document embedding",
			collection: collection,
			documentEmbeddings: []*Document{
				{
					DocumentBase: DocumentBase{
						UUID: document.UUID,
					},
					Embedding: []float32{0.2, 0.3, 0.4, 0.5, 0.6},
				},
			},
			expectedError: "",
		},
		{
			name:       "test update non-existent document embedding",
			collection: collection,
			documentEmbeddings: []*Document{
				{
					DocumentBase: DocumentBase{
						UUID: uuid.New(),
					},
					Embedding: []float32{0.2, 0.3, 0.4, 0.5, 0.6},
				},
			},
			expectedError: "failed to update all document embeddings",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.putDocumentEmbeddings(ctx, testDB, tc.documentEmbeddings)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)

				returnedDocuments, err := tc.collection.getDocuments(ctx, testDB, 0, []*Document{})
				assert.NoError(t, err)
				assert.Equal(t, tc.documentEmbeddings[0].UUID, returnedDocuments[0].UUID)
				assert.Equal(t, tc.documentEmbeddings[0].Embedding, returnedDocuments[0].Embedding)
				assert.Equal(t, tc.documentEmbeddings[0].Embedding, returnedDocuments[0].Embedding)
			}
		})
	}
}
