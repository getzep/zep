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

// End to end with local embedding
func TestDocumentSearchWithIndexEndToEnd(t *testing.T) {
	gofakeit.Seed(0)
	ctx, done := context.WithCancel(testCtx)

	appState.Config.Extractors.Documents.Embeddings.Service = "openai"
	appState.Config.Extractors.Documents.Embeddings.Dimensions = 1536

	collectionName := testutils.GenerateRandomString(16)

	// Create channels
	documentStore, err := NewDocumentStore(
		ctx,
		appState,
		testDB,
	)
	assert.NoError(t, err)

	appState.DocumentStore = documentStore

	// create documents
	docCollection, err := newDocumentCollectionWithDocs(ctx, collectionName,
		500, false, true, 1536)
	assert.NoError(t, err)

	limit := 5
	searchPayload := &models.DocumentSearchPayload{
		Text:           gofakeit.HipsterParagraph(2, 2, 12, " "),
		CollectionName: docCollection.collection.Name,
	}
	// Search for a document
	searchResults, err := documentStore.SearchCollection(
		ctx,
		searchPayload,
		limit,
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

func TestReRankMMR(t *testing.T) {
	// Initialize a documentSearchOperation with a searchPayload of type MMR
	dso := &documentSearchOperation{
		searchPayload: &models.DocumentSearchPayload{
			SearchType: models.SearchTypeMMR,
			MMRLambda:  0.5,
		},
		queryVector: []float32{0.1, 0.2, 0.3},
		limit:       2,
	}

	// Create a slice of SearchDocumentResult
	results := []models.SearchDocumentResult{
		{
			Document: &models.Document{
				DocumentBase: models.DocumentBase{
					DocumentID: "doc1",
				},
				Embedding: []float32{0.1, 0.2, 0.3},
			},
			Score: 1.0,
		},
		{
			Document: &models.Document{
				DocumentBase: models.DocumentBase{
					DocumentID: "doc2",
				},
				Embedding: []float32{0.4, 0.5, 0.6},
			},
			Score: 0.4,
		},
		{
			Document: &models.Document{
				DocumentBase: models.DocumentBase{
					DocumentID: "doc3",
				},
				Embedding: []float32{0.7, 0.8, 0.9},
			},
			Score: 0.2,
		},
		{
			Document: &models.Document{
				DocumentBase: models.DocumentBase{
					DocumentID: "doc4",
				},
				Embedding: []float32{0.1, 0.2, 0.4},
			},
			Score: 0.8,
		},
	}

	// Call reRankMMR method
	rankedResults, err := dso.reRankMMR(results)

	// Assert no error was returned
	assert.NoError(t, err)

	// Assert that the results have been reranked correctly
	assert.Equal(t, 2, len(rankedResults))
	assert.Equal(t, "doc1", rankedResults[0].Document.DocumentID)
	assert.Equal(t, "doc2", rankedResults[1].Document.DocumentID)
}
