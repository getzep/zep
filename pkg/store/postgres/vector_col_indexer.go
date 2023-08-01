package postgres

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sync"

	"github.com/getzep/zep/pkg/models"

	"github.com/uptrace/bun"
)

// reference: https://github.com/pgvector/pgvector#indexing

const EmbeddingColName = "embedding"

// MinRowsForIndex is the minimum number of rows required to create an index. The pgvector docs
// recommend creating the index after a representative sample of data is loaded. This is a guesstimate.
const MinRowsForIndex = 10000

// DefaultDistanceFunction is the default distance function to use for indexing. Using cosine distance
// function by default in order to support both normalized and non-normalized embeddings.
// A future improvement would be to use a the inner product distance function for normalized embeddings.
const DefaultDistanceFunction = "cosine"

// IndexMutexMap stores a mutex for each collection.
var IndexMutexMap = make(map[string]*sync.Mutex)

type VectorColIndex struct {
	appState   *models.AppState
	Collection models.DocumentCollection
	ColName    string
	RowCount   int
	ListCount  int
	ProbeCount int
}

func (vci *VectorColIndex) CountRows(ctx context.Context) error {
	client, ok := vci.appState.DocumentStore.GetClient().(*bun.DB)
	if !ok {
		return fmt.Errorf("failed to get bun.DB client")
	}

	count, err := client.NewSelect().
		ModelTableExpr("?", bun.Ident(vci.Collection.TableName)).
		Count(ctx)
	if err != nil {
		return fmt.Errorf("error counting rows: %w", err)
	}

	vci.RowCount = count

	return nil
}

// CalculateListCount calculates the number of lists to use for the index.
func (vci *VectorColIndex) CalculateListCount() error {
	if vci.RowCount <= 0 {
		return fmt.Errorf("rows must be greater than 0")
	}

	switch {
	case vci.RowCount <= 1000:
		vci.ListCount = 1
	case vci.RowCount <= 1_000_000:
		vci.ListCount = vci.RowCount / 1000
	default:
		vci.ListCount = int(math.Sqrt(float64(vci.RowCount)))
	}

	return nil
}

func (vci *VectorColIndex) CalculateProbes() error {
	// sqrt(lists)
	if vci.ListCount <= 0 {
		return errors.New("lists must be greater than 0")
	}
	vci.ProbeCount = int(math.Sqrt(float64(vci.ListCount)))

	return nil
}

func (vci *VectorColIndex) CreateIndex(ctx context.Context, force bool) error {
	// Check if a mutex already exists for this collection. If not, create one.
	if _, ok := IndexMutexMap[vci.Collection.Name]; !ok {
		IndexMutexMap[vci.Collection.Name] = &sync.Mutex{}
	}
	// Lock the mutex for this collection.
	IndexMutexMap[vci.Collection.Name].Lock()
	defer IndexMutexMap[vci.Collection.Name].Unlock()

	if vci.Collection.DistanceFunction != "cosine" {
		return fmt.Errorf("only cosine distance function is currently supported")
	}

	// If this is not a forced index creation, check if there are enough rows to create an index.
	if !force && vci.RowCount < MinRowsForIndex {
		return fmt.Errorf("not enough rows to create index")
	}

	db, ok := vci.appState.DocumentStore.GetClient().(*bun.DB)
	if !ok {
		return fmt.Errorf("failed to get bun.DB db")
	}

	indexName := fmt.Sprintf("%s_%s_idx", vci.Collection.TableName, vci.ColName)

	// Drop index if it exists
	// We're using CONCURRENTLY for both drop and index operations. This means we can't run them in a transaction.
	_, err := db.ExecContext(
		ctx,
		"DROP INDEX CONCURRENTLY IF EXISTS ?",
		bun.Ident(indexName),
	)
	if err != nil {
		return fmt.Errorf("error dropping index: %w", err)
	}

	// currently only supports cosine distance ops
	_, err = db.ExecContext(
		ctx,
		"CREATE INDEX CONCURRENTLY ON ? USING ivfflat (embedding vector_cosine_ops) WITH (lists = ?)",
		bun.Ident(vci.Collection.TableName),
		vci.ListCount,
	)
	if err != nil {
		return fmt.Errorf("error creating index: %w", err)
	}

	// Set Collection's IsIndexed flag to true
	collection, err := vci.appState.DocumentStore.GetCollection(ctx, vci.Collection.Name)
	if err != nil {
		return fmt.Errorf("error getting collection: %w", err)
	}
	collection.IsIndexed = true
	collection.ProbeCount = vci.ProbeCount
	collection.ListCount = vci.ListCount
	err = vci.appState.DocumentStore.UpdateCollection(ctx, collection)
	if err != nil {
		return fmt.Errorf("error updating collection: %w", err)
	}

	return nil
}

func NewVectorColIndex(
	ctx context.Context,
	appState *models.AppState,
	collection models.DocumentCollection,
) (*VectorColIndex, error) {
	vci := &VectorColIndex{
		appState:   appState,
		Collection: collection,
		ColName:    EmbeddingColName,
	}

	err := vci.CountRows(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to count rows: %w", err)
	}

	err = vci.CalculateListCount()
	if err != nil {
		return nil, fmt.Errorf("failed to calculate list count: %w", err)
	}

	err = vci.CalculateProbes()
	if err != nil {
		return nil, fmt.Errorf("failed to calculate probes: %w", err)
	}

	return vci, nil
}
