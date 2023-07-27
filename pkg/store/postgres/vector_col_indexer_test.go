package postgres

import (
	"context"
	"math"
	"testing"

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

	collection := NewTestCollectionDAO(384)
	err := collection.Create(ctx)
	assert.NoError(t, err)

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

	vci, err := NewVectorColIndex(
		ctx,
		appState,
		collection.TableName,
		DefaultDistanceFunction,
	)
	assert.NoError(t, err)

	vci.RowCount = 1000

	err = vci.CreateIndex(context.Background(), 0)
	assert.NoError(t, err)

	err = documentStore.Shutdown(ctx)
	assert.NoError(t, err)

	done()
}
