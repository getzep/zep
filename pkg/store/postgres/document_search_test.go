package postgres

import (
	"context"
	"testing"

	"github.com/brianvoe/gofakeit/v6"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

// TODO: Unit test documentSearchOperation
// TODO: Test non-happy paths

func TestDocumentSearchWithIndexEndToEnd(t *testing.T) {
	gofakeit.Seed(0)
	ctx, done := context.WithCancel(testCtx)

	collectionName := testutils.GenerateRandomString(16)

	// Create channels
	docEmbeddingUpdateTaskCh := make(chan []models.DocEmbeddingUpdate, 5)
	docEmbeddingTaskCh := make(chan []models.DocEmbeddingTask, 5)
	documentStore, err := NewDocumentStore(
		appState,
		testDB,
		docEmbeddingUpdateTaskCh,
		docEmbeddingTaskCh,
	)
	assert.NoError(t, err)

	appState.DocumentStore = documentStore

	// create documents
	docCollection, err := newDocumentCollectionWithDocs(ctx, collectionName,
		500, false, true, 384)
	assert.NoError(t, err)

	collection := docCollection.collection.DocumentCollection

	// create index
	vci, err := NewVectorColIndex(
		ctx,
		appState,
		collection,
	)
	assert.NoError(t, err)

	err = vci.CreateIndex(context.Background(), true)
	assert.NoError(t, err)

	// Set Collection's IsIndexed flag to true
	col, err := documentStore.GetCollection(ctx, vci.Collection.Name)
	assert.NoError(t, err)
	assert.Equal(t, true, col.IsIndexed)
	assert.True(t, col.ProbeCount > 0)
	assert.True(t, col.ListCount > 0)

	limit := 5
	searchPayload := &models.DocumentSearchPayload{
		Text:           gofakeit.HipsterParagraph(2, 2, 12, " "),
		CollectionName: collectionName,
	}
	// Search for a document
	searchResults, err := documentStore.SearchCollection(
		ctx,
		searchPayload,
		limit,
		false,
		0,
		0,
	)
	assert.NoError(t, err)
	assert.Equal(t, limit, len(searchResults.Results))
	assert.Equal(t, limit, searchResults.ResultCount)
	assert.NotEmpty(t, searchResults.QueryVector)

	for i := range searchResults.Results {
		assert.NotEmpty(t, searchResults.Results[i].Embedding)
		assert.NotEmpty(t, searchResults.Results[i].Content)
		assert.NotEmpty(t, searchResults.Results[i].Metadata)
		assert.NotEmpty(t, searchResults.Results[i].Score)
		assert.NotEmpty(t, searchResults.Results[i].DocumentID)
	}

	err = documentStore.Shutdown(ctx)
	assert.NoError(t, err)

	done()
}
