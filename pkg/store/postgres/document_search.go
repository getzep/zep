package postgres

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/store"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/pkg/models"
)

const DefaultDocumentSearchLimit = 20
const MaxParallelWorkersPerGather = 4

func newDocumentSearchOperation(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	searchPayload *models.DocumentSearchPayload,
	collection *models.DocumentCollection,
	limit int,
	withMMR bool,
) *documentSearchOperation {
	if limit <= 0 {
		limit = DefaultDocumentSearchLimit
	}

	return &documentSearchOperation{
		ctx:           ctx,
		appState:      appState,
		db:            db,
		searchPayload: searchPayload,
		collection:    collection,
		limit:         limit,
		withMMR:       withMMR,
	}
}

type documentSearchOperation struct {
	ctx           context.Context
	appState      *models.AppState
	db            *bun.DB
	searchPayload *models.DocumentSearchPayload
	collection    *models.DocumentCollection
	queryVector   []float32
	limit         int
	withMMR       bool
}

func (dso *documentSearchOperation) Execute() (*models.DocumentSearchResultPage, error) {
	var results []models.SearchDocumentQuery

	var count int
	var err error

	// run in transaction to set LOCAL
	err = dso.db.RunInTx(dso.ctx, &sql.TxOptions{}, func(ctx context.Context, tx bun.Tx) error {
		if dso.collection.IsIndexed {
			_, err = tx.Exec("SET LOCAL ivfflat.probes = ?", dso.collection.ProbeCount)
		} else {
			_, err = tx.Exec("SET LOCAL max_parallel_workers_per_gather = ?", MaxParallelWorkersPerGather)
		}
		if err != nil {
			return fmt.Errorf("error setting probes: %w", err)
		}
		count, err = dso.execQuery(tx, &results)
		if err != nil {
			return fmt.Errorf("error executing query: %w", err)
		}

		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("error executing search: %w", err)
	}

	resultPage := &models.DocumentSearchResultPage{
		Results:     searchResultsFromSearchQueries(results),
		QueryVector: dso.queryVector,
		ResultCount: count,
	}

	return resultPage, nil
}

// execQuery executes the query and scans the results into the provided results slice. It accepts a bun DB or Tx.
func (dso *documentSearchOperation) execQuery(
	db bun.IDB,
	results *[]models.SearchDocumentQuery,
) (int, error) {
	query, err := dso.buildQuery(db)
	if err != nil {
		return 0, fmt.Errorf("error building query %w", err)
	}

	err = query.Scan(dso.ctx, results)
	if err != nil {
		if strings.Contains(err.Error(), "different vector dimensions") {
			return 0, store.NewEmbeddingMismatchError(err)
		}
		return 0, fmt.Errorf("error scanning query %w", err)
	}

	count := len(*results)

	if count == 0 {
		return 0, models.NewNotFoundError("no results found")
	}

	return count, nil
}

func (dso *documentSearchOperation) buildQuery(db bun.IDB) (*bun.SelectQuery, error) {
	m := &[]models.SearchDocumentQuery{}
	query := db.NewSelect().Model(m).
		ModelTableExpr("?", bun.Ident(dso.collection.TableName)).
		Column("*").
		WhereAllWithDeleted().
		Where("deleted_at IS NULL") // Manually add as ModelTableExpr confuses bun

	// Add the vector column if either text or embedding is set
	if dso.searchPayload.Text != "" || len(dso.searchPayload.Embedding) != 0 {
		var v pgvector.Vector
		var err error
		if len(dso.searchPayload.Embedding) != 0 {
			v = pgvector.NewVector(dso.searchPayload.Embedding)
		} else {
			v, err = dso.getDocQueryVector(dso.searchPayload.Text)
			if err != nil {
				return nil, fmt.Errorf("error getting query vector %w", err)
			}
		}
		dso.queryVector = v.Slice()

		// Score is cosine similarity normalized to 1
		query = query.ColumnExpr("((1 - (embedding <=> ?))/2 + 0.5) AS score", v)
	}

	if len(dso.searchPayload.Metadata) > 0 {
		var err error
		query, err = dso.applyDocsMetadataFilter(query, dso.searchPayload.Metadata)
		if err != nil {
			return nil, fmt.Errorf("error applying metadata filter: %w", err)
		}
	}

	// Add LIMIT
	// If we're using MMR, we need to add a limit of 2x the requested limit to allow for the MMR
	// algorithm to rerank and filter out results.
	limit := dso.limit
	if dso.withMMR {
		limit *= 2
	}
	query = query.Limit(limit)

	// Order by dist - required for index to be used.
	if dso.searchPayload.Text != "" || len(dso.searchPayload.Embedding) != 0 {
		query.Order("score DESC")
	}

	return query, nil
}

// getDocQueryVector returns the vector for the query text.
func (dso *documentSearchOperation) getDocQueryVector(
	queryText string,
) (pgvector.Vector, error) {
	documentType := "document"
	model, err := llms.GetEmbeddingModel(dso.appState, documentType)
	if err != nil {
		return pgvector.Vector{}, fmt.Errorf("failed to get document embedding model %w", err)
	}

	e, err := llms.EmbedTexts(dso.ctx, dso.appState, model, documentType, []string{queryText})
	if err != nil {
		return pgvector.Vector{}, fmt.Errorf("failed to embed query %w", err)
	}

	v := pgvector.NewVector(e[0])
	return v, nil
}

// applyDocsMetadataFilter applies the metadata filter to the query.
func (dso *documentSearchOperation) applyDocsMetadataFilter(
	query *bun.SelectQuery,
	metadata map[string]interface{},
) (*bun.SelectQuery, error) {
	qb := query.QueryBuilder()

	if where, ok := metadata["where"]; ok {
		j, err := json.Marshal(where)
		if err != nil {
			return nil, fmt.Errorf("error marshalling metadata %w", err)
		}

		var jq JSONQuery
		err = json.Unmarshal(j, &jq)
		if err != nil {
			return nil, fmt.Errorf("error unmarshalling metadata %w", err)
		}
		qb = parseDocumentJSONQuery(qb, &jq, false)
	}

	query = qb.Unwrap().(*bun.SelectQuery)

	return query, nil
}

func searchResultsFromSearchQueries(s []models.SearchDocumentQuery) []models.DocumentSearchResult {
	result := make([]models.DocumentSearchResult, len(s))

	for i := range s {
		result[i] = models.DocumentSearchResult{
			DocumentResponse: &models.DocumentResponse{
				UUID:       s[i].UUID,
				CreatedAt:  s[i].CreatedAt,
				UpdatedAt:  s[i].UpdatedAt,
				DocumentID: s[i].DocumentID,
				Content:    s[i].Content,
				Metadata:   s[i].Metadata,
				Embedding:  s[i].Embedding,
				IsEmbedded: s[i].IsEmbedded,
			},
			Score: s[i].Score,
		}
	}

	return result
}
