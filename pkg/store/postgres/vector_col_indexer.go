package postgres

import (
	"context"
	"errors"
	"fmt"
	"math"

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

type VectorColIndex struct {
	appState         *models.AppState
	TableName        string
	ColName          string
	DistanceFunction string
	RowCount         int
	ListCount        int
	ProbeCount       int
}

func (vci *VectorColIndex) CountRows(ctx context.Context) error {
	client, ok := vci.appState.DocumentStore.GetClient().(*bun.DB)
	if !ok {
		return fmt.Errorf("failed to get bun.DB client")
	}

	count, err := client.NewSelect().
		ModelTableExpr(vci.TableName).
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
	if vci.RowCount <= 1000 {
		vci.ListCount = 1
		return nil
	}
	// rows / 1000 for up to 1M rows and sqrt(rows) for over 1M rows
	if vci.RowCount <= 1_000_000 {
		vci.ListCount = int(vci.RowCount / 1000)
		return nil
	}
	vci.ListCount = int(math.Sqrt(float64(vci.RowCount)))

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

func (vci *VectorColIndex) CreateIndex(ctx context.Context, minRows int) error {
	if vci.DistanceFunction != "cosine" {
		return fmt.Errorf("only cosine distance function is currently supported")
	}

	// If minRows is 0, use the default min rows. Added to support testing.
	minRowCount := minRows
	if minRowCount == 0 {
		minRowCount = MinRowsForIndex
	}
	if vci.RowCount < minRowCount {
		return fmt.Errorf("not enough rows to create index")
	}

	db, ok := vci.appState.DocumentStore.GetClient().(*bun.DB)
	if !ok {
		return fmt.Errorf("failed to get bun.DB db")
	}

	indexName := fmt.Sprintf("%s_%s_idx", vci.TableName, vci.ColName)

	// Drop index if it exists
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
		bun.Ident(vci.TableName),
		vci.ListCount,
	)
	if err != nil {
		return fmt.Errorf("error creating index: %w", err)
	}

	return nil
}

func NewVectorColIndex(
	ctx context.Context,
	appState *models.AppState,
	tableName string,
	distanceFunction string,
) (*VectorColIndex, error) {
	vci := &VectorColIndex{
		appState:         appState,
		TableName:        tableName,
		ColName:          EmbeddingColName,
		DistanceFunction: distanceFunction,
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
