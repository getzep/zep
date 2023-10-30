package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/models"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/google/uuid"

	"github.com/stretchr/testify/assert"
)

func NewTestCollectionDAO(embeddingWidth int) DocumentCollectionDAO {
	return DocumentCollectionDAO{
		DocumentCollection: models.DocumentCollection{
			Name:                testutils.GenerateRandomString(10),
			EmbeddingDimensions: embeddingWidth,
			IsAutoEmbedded:      true,
		},
		db:       testDB,
		appState: appState,
	}
}

func TestCollectionCreate(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(10)

	testCases := []struct {
		name          string
		collection    *DocumentCollectionDAO
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
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(10)
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
	assert.Less(t, collection.CreatedAt, collection.UpdatedAt)
}

func TestCollectionGetByName(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(10)

	err = collection.Create(ctx)
	assert.NoError(t, err)

	testCases := []struct {
		name             string
		collection       DocumentCollectionDAO
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
			collection:       NewTestCollectionDAO(10),
			expectedError:    "not found",
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
				assert.NotZero(t, tc.collection.UUID)
				assert.NotZero(t, tc.collection.CreatedAt)
				assert.Equal(t, collection.EmbeddingDimensions, tc.collection.EmbeddingDimensions)
				assert.Equal(t, collection.IsAutoEmbedded, tc.collection.IsAutoEmbedded)
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
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	var collectionsToCreate []DocumentCollectionDAO

	for i := 0; i < 3; i++ {
		collection := NewTestCollectionDAO(10)
		err = collection.Create(ctx)
		assert.NoError(t, err)

		collectionsToCreate = append(collectionsToCreate, collection)
	}

	retrievedCollections, err := collectionsToCreate[0].GetAll(ctx)
	assert.NoError(t, err)

	// Compare lengths of created and retrieved collections
	assert.Equal(t, len(collectionsToCreate), len(retrievedCollections))

	retrievedMap := make(map[string]models.DocumentCollection, len(retrievedCollections))
	for _, m := range retrievedCollections {
		retrievedMap[m.Name] = m
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
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(10)

	err = collection.Create(ctx)
	assert.NoError(t, err)

	testCases := []struct {
		name                string
		collection          DocumentCollectionDAO
		expectedErrorString string
		expectedNotFound    bool
	}{
		{
			name:                "test Delete of existing collection",
			collection:          collection,
			expectedErrorString: "",
		},
		{
			name:                "test when collection does not exist",
			collection:          NewTestCollectionDAO(10),
			expectedErrorString: "not found",
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
	expectedDocuments []models.Document,
	actualUUIDs []uuid.UUID,
) {
	// create a map of expected UUIDs
	expectedUUIDs := make(map[uuid.UUID]struct{}, len(expectedDocuments))
	for _, d := range expectedDocuments {
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
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(3)
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.Document, 2)
	for i := range documents {
		documents[i] = models.Document{
			DocumentBase: models.DocumentBase{
				DocumentID: testutils.GenerateRandomString(10),
				Content:    testutils.GenerateRandomString(10),
				Metadata:   map[string]interface{}{"key": testutils.GenerateRandomString(3)},
			},
			Embedding: []float32{0.1, 0.2, 0.3},
		}
	}

	testCases := []struct {
		name          string
		collection    DocumentCollectionDAO
		documents     []models.Document
		expectedError string
	}{
		{
			name:          "test Create documents into an existing collection",
			collection:    collection,
			documents:     documents,
			expectedError: "",
		},
		{
			name:          "test Create documents into a non-existent collection",
			collection:    NewTestCollectionDAO(3),
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

				// Compare maps
				for i, expectedDoc := range tc.documents {
					returnedDoc := returnedDocuments[i]
					assert.Equal(t, expectedDoc.DocumentID, returnedDoc.DocumentID, "DocumentID mismatch for DocumentID %s", i)
					assert.Equal(t, expectedDoc.Content, returnedDoc.Content, "Content mismatch for DocumentID %s", i)
					assert.Equal(t, expectedDoc.Metadata, returnedDoc.Metadata, "Metadata mismatch for DocumentID %s", i)
					assert.Equal(t, expectedDoc.Embedding, returnedDoc.Embedding, "Embedding mismatch for DocumentID %s", i)
				}
			}
		})
	}
}

func TestDocumentCollectionUpdateDocuments(t *testing.T) {
	ctx := context.Background()
	CleanDB(t, testDB)
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(5)
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.Document, 10)
	for i := range documents {
		documents[i] = models.Document{
			DocumentBase: models.DocumentBase{
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

	updatedDocuments := make([]models.Document, 10)
	for i := range updatedDocuments {
		updatedDocuments[i] = models.Document{
			DocumentBase: models.DocumentBase{
				UUID:       documents[i].UUID,
				DocumentID: testutils.GenerateRandomString(10),
				Metadata:   documents[i].Metadata,
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

	for i := range updatedDocuments {
		updatedDoc := updatedDocuments[i]
		returnedDoc := returnedDocuments[i]

		assert.Equal(
			t,
			updatedDoc.DocumentID,
			returnedDoc.DocumentID,
			"Content mismatch for DocumentID %s",
			i,
		)

		assert.Equal(
			t,
			updatedDoc.Metadata,
			returnedDoc.Metadata,
			"Metadata mismatch for Metadata %s",
			i,
		)

		assert.Equal(
			t,
			updatedDoc.Embedding,
			returnedDoc.Embedding,
			"Metadata mismatch for TextData %s",
			i,
		)
	}
}

func getDocumentIDs(docs []models.Document) ([]string, error) {
	ids := make([]string, len(docs))
	for i, doc := range docs {
		ids[i] = doc.DocumentID
	}
	return ids, nil
}

func TestDocumentCollectionGetDocuments(t *testing.T) {
	ctx := context.Background()

	CleanDB(t, testDB)
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(10)
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.Document, 10)
	for i := range documents {
		documents[i] = models.Document{
			DocumentBase: models.DocumentBase{
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
		collection    DocumentCollectionDAO
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
						expectedDoc := documents[i]
						returnedDoc := returnedDocuments[i]
						assert.Equal(t, expectedDoc.UUID, returnedDoc.UUID)
					}
				default:
					assert.Equal(t, len(documents), len(returnedDocuments))
					for i := range documents {
						expectedDoc := documents[i]
						returnedDoc := returnedDocuments[i]
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
	err := CreateSchema(ctx, appState, testDB)
	assert.NoError(t, err)

	collection := NewTestCollectionDAO(10)
	err = collection.Create(ctx)
	assert.NoError(t, err)

	documents := make([]models.Document, 2)
	for i := range documents {
		documents[i] = models.Document{
			DocumentBase: models.DocumentBase{
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

	expectedError := "documents not found"

	testCases := []struct {
		name          string
		collection    DocumentCollectionDAO
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
			expectedError: expectedError,
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
				assert.ErrorContains(t, err, expectedError)
				assert.Equal(t, 0, len(returnedDocuments))
			}
		})
	}
}

func getDocumentUUIDList(documents []models.Document) ([]uuid.UUID, error) {
	uuids := make([]uuid.UUID, len(documents))
	for i, doc := range documents {
		if doc.UUID == uuid.Nil {
			continue
		}
		uuids[i] = doc.UUID
	}
	return uuids, nil
}
