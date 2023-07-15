package postgres

import (
	"context"
	"errors"
	"testing"

	"github.com/getzep/zep/pkg/models"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/google/uuid"

	"github.com/stretchr/testify/assert"
)

func TestCollectionCreate(t *testing.T) {
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
		expectedError string
	}{
		{
			name:          "test create collection",
			collection:    &collection,
			expectedError: "",
		},
		{
			name:          "should fail when collection already exists.",
			collection:    &collection,
			expectedError: "already exists",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.Create(ctx)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
			}
		})
	}
}

func TestCollectionUpdate(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 10,
		db:                  testDB,
	}
	err = collection.Create(ctx)
	assert.NoError(t, err)

	// Update the collection
	expectedDimensions := 20
	collection.EmbeddingDimensions = expectedDimensions
	err = collection.Update(ctx)
	assert.NoError(t, err)

	// Retrieve the collection again and check that the update was successful
	err = collection.GetByName(ctx)
	assert.NoError(t, err)
	assert.Equal(t, expectedDimensions, collection.EmbeddingDimensions)
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

	err = collection.Create(ctx)
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
		err = collection.Create(ctx)
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

	err = collection.Create(ctx)
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

func compareDocumentUUIDs(
	t *testing.T,
	expectedDocuments []models.DocumentInterface,
	actualUUIDs []uuid.UUID,
) {
	// create a map of expected UUIDs
	expectedUUIDs := make(map[uuid.UUID]struct{}, len(expectedDocuments))
	for _, doc := range expectedDocuments {
		d := doc.(*Document)
		expectedUUIDs[d.UUID] = struct{}{}
	}

	// compare sets of tc.documents UUIDs with uuids
	for _, id := range actualUUIDs {
		if _, ok := expectedUUIDs[id]; !ok {
			assert.Failf(t, "expected UUID missing", "expected UUID missing: %s", id)
			break
		}
	}
}

func TestDocumentCollectionCreateDocuments(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 5,
		db:                  testDB,
	}
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.DocumentInterface, 10)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				DocumentID: testutils.GenerateRandomString(10),
				Content:    testutils.GenerateRandomString(10),
				Metadata:   map[string]interface{}{"key": testutils.GenerateRandomString(3)},
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
			name:          "test Create documents into an existing collection",
			collection:    collection,
			documents:     documents,
			expectedError: "",
		},
		{
			name: "test Create documents into a non-existent collection",
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
			uuids, err := tc.collection.CreateDocuments(ctx, tc.documents)

			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				assert.Equal(t, len(tc.documents), len(uuids))
				compareDocumentUUIDs(t, tc.documents, uuids)

				returnedDocuments, err := tc.collection.GetDocuments(ctx, 0, nil, nil)
				assert.NoError(t, err)

				assert.Equal(t, len(tc.documents), len(returnedDocuments))
				// Convert slices to maps
				expected := make(map[string]*Document, len(tc.documents))
				actual := make(map[string]*Document, len(returnedDocuments))
				for _, doc := range tc.documents {
					expected[doc.(*Document).DocumentID] = doc.(*Document)
				}
				for _, doc := range returnedDocuments {
					actual[doc.(*Document).DocumentID] = doc.(*Document)
				}

				// Compare maps
				for id, expectedDoc := range expected {
					actualDoc, ok := actual[id]
					assert.True(t, ok, "DocumentID %s not found in actual", id)
					assert.Equal(t, expectedDoc.Content, actualDoc.Content, "Content mismatch for DocumentID %s", id)
					assert.Equal(t, expectedDoc.Metadata, actualDoc.Metadata, "Metadata mismatch for DocumentID %s", id)
				}
			}
		})
	}
}

func TestDocumentCollectionUpdateDocuments(t *testing.T) {
	ctx := context.Background()
	CleanDB(t, testDB)
	err := ensurePostgresSetup(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := DocumentCollection{
		Name:                testutils.GenerateRandomString(10),
		EmbeddingDimensions: 5,
		db:                  testDB,
	}
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.DocumentInterface, 10)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				DocumentID: testutils.GenerateRandomString(10),
				Content:    testutils.GenerateRandomString(10),
				Metadata:   map[string]interface{}{"key": testutils.GenerateRandomString(3)},
			},
		}
	}
	uuids, err := collection.CreateDocuments(ctx, documents)
	assert.NoError(t, err)
	assert.Equal(t, len(documents), len(uuids))
	compareDocumentUUIDs(t, documents, uuids)

	updatedDocuments := make([]models.DocumentInterface, 10)
	for i := range updatedDocuments {
		updatedDocuments[i] =
			&Document{
				DocumentBase: DocumentBase{
					UUID:     uuids[i],
					Metadata: map[string]interface{}{"key": testutils.GenerateRandomString(3)},
				},
				Embedding: []float32{0.1, 0.2, 0.3, 0.4, 0.5},
			}
	}

	err = collection.UpdateDocuments(ctx, updatedDocuments)
	assert.NoError(t, err)

	returnedDocuments, err := collection.GetDocuments(ctx, 0, nil, nil)
	assert.NoError(t, err)

	assert.Equal(t, len(documents), len(returnedDocuments))
	compareDocumentUUIDs(t, returnedDocuments, uuids)

	for i, ed := range updatedDocuments {
		expectedDoc := ed.(*Document)
		originalDoc := documents[i].(*Document)
		actualDoc := returnedDocuments[i].(*Document)

		assert.Equal( // This should not change
			t,
			originalDoc.DocumentID,
			actualDoc.DocumentID,
			"Content mismatch for DocumentID %s",
			i,
		)

		assert.Equal(
			t,
			expectedDoc.Metadata,
			actualDoc.Metadata,
			"Metadata mismatch for Metadata %s",
			i,
		)

		assert.Equal(
			t,
			expectedDoc.Embedding,
			actualDoc.Embedding,
			"Metadata mismatch for MessageEmbedding %s",
			i,
		)
	}
}

func getDocumentIDs(docs []models.DocumentInterface) ([]string, error) {
	ids := make([]string, len(docs))
	for i, doc := range docs {
		if doc == nil {
			return nil, errors.New("nil document")
		}
		d := doc.(*Document)
		ids[i] = d.DocumentID
	}
	return ids, nil
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
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.DocumentInterface, 10)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				Content:    testutils.GenerateRandomString(10),
				DocumentID: testutils.GenerateRandomString(10),
				Metadata:   map[string]interface{}{"key": testutils.GenerateRandomString(3)},
			},
		}
	}

	uuids, err := collection.CreateDocuments(ctx, documents)
	assert.NoError(t, err)
	assert.Equal(t, len(documents), len(uuids))

	documentIDs, err := getDocumentIDs(documents)
	assert.NoError(t, err)

	testCases := []struct {
		name          string
		collection    DocumentCollection
		limit         int
		uuids         []uuid.UUID
		documentIDs   []string
		expectedError string
	}{

		{
			name:          "test get all documents",
			collection:    collection,
			limit:         0,
			expectedError: "",
		},
		{
			name:          "test get limited number of documents",
			collection:    collection,
			limit:         5,
			expectedError: "",
		},
		{
			name:          "test get specific documents by UUID",
			collection:    collection,
			limit:         0,
			uuids:         uuids[:5],
			documentIDs:   nil,
			expectedError: "",
		},
		{
			name:          "test get specific documents by ID",
			collection:    collection,
			limit:         0,
			uuids:         nil,
			documentIDs:   documentIDs[:5],
			expectedError: "",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			returnedDocuments, err := tc.collection.GetDocuments(
				ctx,
				tc.limit,
				tc.uuids,
				tc.documentIDs,
			)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				switch {
				case tc.limit > 0:
					assert.True(t, len(returnedDocuments) <= tc.limit)
				case len(tc.uuids) > 0 || len(tc.documentIDs) > 0:
					docCount := len(tc.uuids)
					if docCount == 0 {
						docCount = len(tc.documentIDs)
					}
					assert.Equal(t, docCount, len(returnedDocuments))
					for i := range returnedDocuments {
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
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.DocumentInterface, 2)
	for i := range documents {
		documents[i] = &Document{
			DocumentBase: DocumentBase{
				Content:    testutils.GenerateRandomString(10),
				DocumentID: testutils.GenerateRandomString(10),
				Metadata:   map[string]interface{}{"key": testutils.GenerateRandomString(3)},
			},
		}
	}

	uuids, err := collection.CreateDocuments(ctx, documents)
	assert.NoError(t, err)
	assert.Equal(t, len(documents), len(uuids))

	documentUUIDs, err := getDocumentUUIDList(documents)
	assert.NoError(t, err)

	nonexistantUUIDs := []uuid.UUID{uuid.New(), uuid.New()}

	testCases := []struct {
		name          string
		collection    DocumentCollection
		documentUUIDs []uuid.UUID
		expectedError string
	}{
		{
			name:          "test Delete existing documents",
			collection:    collection,
			documentUUIDs: documentUUIDs,
			expectedError: "",
		},
		{
			name:          "test Delete non-existent documents",
			collection:    collection,
			documentUUIDs: nonexistantUUIDs,
			expectedError: "not all documents found",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.collection.DeleteDocumentsByUUID(ctx, tc.documentUUIDs)
			if tc.expectedError != "" {
				assert.ErrorContains(t, err, tc.expectedError)
			} else {
				assert.NoError(t, err)
				returnedDocuments, err := tc.collection.GetDocuments(ctx, 0, tc.documentUUIDs, nil)
				assert.NoError(t, err)
				assert.Equal(t, 0, len(returnedDocuments))
			}
		})
	}
}
