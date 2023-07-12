package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/models"

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
		db:                  testDB,
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
			err := tc.collection.Put(ctx)
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
		db:                  testDB,
	}

	err = collection.Put(ctx)
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
			name: "test when collection does not exist",
			collection: DocumentCollection{
				Name: testutils.GenerateRandomString(10),
				db:   testDB,
			},
			expectedError:    "no rows in result set",
			expectedNotFound: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.GetByName(ctx)
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
			db:                  testDB,
		}
		err = collection.Put(ctx)
		assert.NoError(t, err)

		collectionsToCreate = append(collectionsToCreate, collection)
	}

	retrievedCollections, err := collectionsToCreate[0].GetAll(ctx)
	assert.NoError(t, err)

	// Compare lengths of created and retrieved collections
	assert.Equal(t, len(collectionsToCreate), len(retrievedCollections))

	retrievedMap := make(map[string]*DocumentCollection, len(retrievedCollections))
	for _, retrievedCollInterface := range retrievedCollections {
		retrievedColl := retrievedCollInterface.(*DocumentCollection)
		retrievedMap[retrievedColl.Name] = retrievedColl
	}

	// Check each created collection is in retrieved collections
	for _, createdColl := range collectionsToCreate {
		retrievedColl, ok := retrievedMap[createdColl.Name]
		assert.True(t, ok, "Created collection not found in retrieved collections")
		assert.Equal(t, createdColl.Name, retrievedColl.Name)
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
		db:                  testDB,
	}

	err = collection.Put(ctx)
	assert.NoError(t, err)

	testCases := []struct {
		name                string
		collection          DocumentCollection
		expectedErrorString string
		expectedNotFound    bool
	}{
		{
			name:                "test Delete of existing collection",
			collection:          collection,
			expectedErrorString: "",
		},
		{
			name: "test when collection does not exist",
			collection: DocumentCollection{
				Name: testutils.GenerateRandomString(10),
				db:   testDB,
			},
			expectedErrorString: "no rows in result set",
			expectedNotFound:    true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err = tc.collection.Delete(ctx)

			if tc.expectedErrorString != "" {
				assert.Error(t, err)
				assert.ErrorContains(t, err, tc.expectedErrorString)
			} else {
				assert.NoError(t, err)

				// Try to retrieve the deleted collection
				err := tc.collection.GetByName(ctx)
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
		db:                  testDB,
	}
	err = collection.Put(ctx)
	assert.NoError(t, err)

	documents := make([]models.DocumentInterface, 10)
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
		documents     []models.DocumentInterface
		expectedError string
	}{
		{
			name:          "test Put documents into an existing collection",
			collection:    collection,
			documents:     documents,
			expectedError: "",
		},
		{
			name: "test Put documents into a non-existent collection",
			collection: DocumentCollection{
				UUID: uuid.New(),
				Name: "NonExistentCollection",
				db:   testDB,
			},
			documents:     documents,
			expectedError: "failed to get collection",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.PutDocuments(ctx, tc.documents)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)

				returnedDocuments, err := tc.collection.GetDocuments(ctx, 0, nil)
				assert.NoError(t, err)

				assert.Equal(t, len(tc.documents), len(returnedDocuments))
				// Convert slices to maps
				expected := make(map[string]string, len(tc.documents))
				actual := make(map[string]string, len(returnedDocuments))
				for _, doc := range tc.documents {
					expected[doc.(*Document).Content] = doc.(*Document).Content
				}
				for _, doc := range returnedDocuments {
					actual[doc.(*Document).Content] = doc.(*Document).Content
				}

				// Compare maps
				assert.Equal(t, expected, actual)
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
		db:                  testDB,
	}
	err = collection.Put(ctx)
	assert.NoError(t, err)

	documents := make([]models.DocumentInterface, 10)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				Content: testutils.GenerateRandomString(10),
			},
		}
	}

	err = collection.PutDocuments(ctx, documents)
	assert.NoError(t, err)

	testCases := []struct {
		name          string
		collection    DocumentCollection
		limit         int
		documents     []models.DocumentInterface
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
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// Deep copy the documents
			assert.NoError(t, err)
			uuids, err := getDocumentUUIDList(tc.documents)
			assert.NoError(t, err)

			returnedDocuments, err := tc.collection.GetDocuments(
				ctx,
				tc.limit,
				uuids,
			)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				switch {
				case tc.limit > 0:
					assert.True(t, len(returnedDocuments) <= tc.limit)
				case tc.documents != nil:
					assert.Equal(t, len(tc.documents), len(returnedDocuments))
					for i := range tc.documents {
						expectedDoc := documents[i].(*Document)
						returnedDoc := returnedDocuments[i].(*Document)
						assert.Equal(t, expectedDoc.UUID, returnedDoc.UUID)
					}
				default:
					assert.Equal(t, len(documents), len(returnedDocuments))
					for i := range documents {
						expectedDoc := documents[i].(*Document)
						returnedDoc := returnedDocuments[i].(*Document)
						assert.Equal(t, expectedDoc.Content, returnedDoc.Content)
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
		db:                  testDB,
	}
	err = collection.Put(ctx)
	assert.NoError(t, err)

	document := Document{
		DocumentBase: DocumentBase{
			Content: testutils.GenerateRandomString(10),
		},
	}
	err = collection.PutDocuments(ctx, []models.DocumentInterface{&document})
	assert.NoError(t, err)

	testCases := []struct {
		name          string
		collection    DocumentCollection
		documentUUID  uuid.UUID
		expectedError string
	}{
		{
			name:          "test Delete existing document",
			collection:    collection,
			documentUUID:  document.UUID,
			expectedError: "",
		},
		{
			name:          "test Delete non-existent document",
			collection:    collection,
			documentUUID:  uuid.New(),
			expectedError: "document not found",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.DeleteDocumentByUUID(ctx, tc.documentUUID)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, "document not found")
			} else {
				assert.NoError(t, err)
				returnedDocuments, err := tc.collection.GetDocuments(ctx, 0, []uuid.UUID{tc.documentUUID})
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
		db:                  testDB,
	}
	err = collection.Put(ctx)
	assert.NoError(t, err)

	document := Document{
		DocumentBase: DocumentBase{
			Content: testutils.GenerateRandomString(10),
		},
		Embedding: []float32{0.1, 0.2, 0.3, 0.4, 0.5},
	}
	err = collection.PutDocuments(ctx, []models.DocumentInterface{&document})
	assert.NoError(t, err)

	testCases := []struct {
		name               string
		collection         DocumentCollection
		documentEmbeddings []models.DocumentInterface
		expectedError      string
	}{
		{
			name:       "test update existing document embedding",
			collection: collection,
			documentEmbeddings: []models.DocumentInterface{
				&Document{
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
			documentEmbeddings: []models.DocumentInterface{
				&Document{
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
			err := tc.collection.PutDocumentEmbeddings(ctx, tc.documentEmbeddings)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)

				uuids, err := getDocumentUUIDList(tc.documentEmbeddings)
				assert.NoError(t, err)

				returnedDocuments, err := tc.collection.GetDocuments(ctx, 0, uuids)
				assert.NoError(t, err)

				expectedDocument := tc.documentEmbeddings[0].(*Document)
				returnedDocument := returnedDocuments[0].(*Document)

				assert.Equal(t, expectedDocument.UUID, returnedDocument.UUID)
				assert.Equal(t, expectedDocument.Embedding, returnedDocument.Embedding)
				assert.Equal(t, expectedDocument.Embedding, returnedDocument.Embedding)
			}
		})
	}
}
