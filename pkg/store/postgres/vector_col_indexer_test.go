package postgres

import (
	"context"
	"fmt"
	"math"
	"math/rand"
	"testing"
	"time"

	"github.com/brianvoe/gofakeit/v6"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestCalculateListCount(t *testing.T) {
	vci := &VectorColIndex{
		appState: &models.AppState{},
	}

	// Test when RowCount <= 1_000_000
	vci.RowCount = 500_000
	err := vci.CalculateListCount()
	assert.NoError(t, err)
	assert.Equal(t, vci.RowCount/1000, vci.ListCount)

	// Test when RowCount > 1_000_000
	vci.RowCount = 2_000_000
	err = vci.CalculateListCount()
	assert.NoError(t, err)
	assert.Equal(t, int(math.Sqrt(2_000_000)), vci.ListCount)
}

func TestCalculateProbes(t *testing.T) {
	vci := &VectorColIndex{
		appState: &models.AppState{},
	}

	vci.ListCount = 1000
	err := vci.CalculateProbes()
	assert.NoError(t, err)
	assert.Equal(t, int(math.Sqrt(1000)), vci.ProbeCount)
}

func TestCreateIndex(t *testing.T) {
	ctx, done := context.WithCancel(testCtx)

	collectionName := testutils.GenerateRandomString(16)

	docCollection, err := newDocumentCollectionWithDocs(ctx, collectionName,
		500, false, true, 384)
	assert.NoError(t, err)

	collection := docCollection.collection.DocumentCollection

	documentStore, err := NewDocumentStore(
		ctx,
		appState,
		testDB,
	)
	assert.NoError(t, err)

	appState.DocumentStore = documentStore

	vci, err := NewVectorColIndex(
		ctx,
		appState,
		collection,
	)
	assert.NoError(t, err)

	// CreateIndex will add a timeout to the ctx
	err = vci.CreateIndex(context.Background(), true)
	assert.NoError(t, err)

	pollIndexCreation(ctx, documentStore, collectionName, t)

	col, err := documentStore.GetCollection(ctx, vci.Collection.Name)
	assert.NoError(t, err)
	assert.Equal(t, true, col.IsIndexed)
	assert.True(t, col.ProbeCount > 0)
	assert.True(t, col.ListCount > 0)

	err = documentStore.Shutdown(ctx)
	assert.NoError(t, err)

	done()
}

type testDocCollection struct {
	collection DocumentCollectionDAO
	docUUIDs   []uuid.UUID
}

func newDocumentCollectionWithDocs(
	ctx context.Context,
	collectionName string,
	numDocs int,
	autoEmbed bool,
	withRandomEmbeddings bool,
	embeddingWidth int,
) (testDocCollection, error) {
	gofakeit.Seed(0)

	collection := NewTestCollectionDAO(embeddingWidth)
	collection.Name = collectionName
	collection.IsAutoEmbedded = autoEmbed
	err := collection.Create(ctx)
	if err != nil {
		return testDocCollection{}, fmt.Errorf("error creating collection: %w", err)
	}

	embeddings := make([][]float32, numDocs)
	if withRandomEmbeddings {
		embeddings = generateRandomEmbeddings(numDocs, embeddingWidth)
		if err != nil {
			return testDocCollection{}, fmt.Errorf("error generating random embeddings: %w", err)
		}
	}
	documents := make([]models.Document, numDocs)
	for i := 0; i < numDocs; i++ {
		documents[i] = models.Document{
			DocumentBase: models.DocumentBase{
				Content:    gofakeit.HipsterParagraph(2, 2, 12, " "),
				DocumentID: testutils.GenerateRandomString(20),
				Metadata:   gofakeit.Map(),
				IsEmbedded: !autoEmbed,
			},
			Embedding: embeddings[i],
		}
	}
	uuids, err := collection.CreateDocuments(ctx, documents)
	if err != nil {
		return testDocCollection{}, fmt.Errorf("error creating documents: %w", err)
	}

	return testDocCollection{
		collection: collection,
		docUUIDs:   uuids,
	}, nil
}

func generateRandomEmbeddings(embeddingCount int, embeddingWidth int) [][]float32 {
	embeddings := make([][]float32, embeddingCount)
	for i := 0; i < embeddingCount; i++ {
		embedding := make([]float32, embeddingWidth)
		for j := 0; j < embeddingWidth; j++ {
			embedding[j] = rand.Float32()
		}
		embeddings[i] = embedding
	}

	return embeddings
}

func pollIndexCreation(
	ctx context.Context,
	documentStore *DocumentStore,
	collectionName string,
	t *testing.T,
) {
	timeout := time.After(10 * time.Minute)
	tick := time.Tick(500 * time.Millisecond)
Loop:
	for {
		select {
		case <-timeout:
			t.Fatal("timed out waiting for index to be created")
		case <-tick:
			col, err := documentStore.GetCollection(ctx, collectionName)
			if err != nil {
				t.Fatal("error getting collection: ", err)
			}
			if col.IsIndexed {
				break Loop
			}
		}
	}
}
