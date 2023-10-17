package postgres

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/search"
	"github.com/getzep/zep/pkg/store"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/pkg/models"
)

const DefaultEFSearch = 100
const DefaultDocumentSearchLimit = 20
const MaxParallelWorkersPerGather = 4

func newDocumentSearchOperation(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	searchPayload *models.DocumentSearchPayload,
	collection *models.DocumentCollection,
	limit int,
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
}

func (dso *documentSearchOperation) Execute() (*models.DocumentSearchResultPage, error) {
	var results []models.SearchDocumentResult

	var count int
	var err error

	// run in transaction to set LOCAL
	err = dso.db.RunInTx(dso.ctx, &sql.TxOptions{}, func(ctx context.Context, tx bun.Tx) error {
		switch dso.collection.IndexType {
		case "ivfflat":
			if dso.collection.IsIndexed {
				_, err = tx.Exec("SET LOCAL ivfflat.probes = ?", dso.collection.ProbeCount)
			} else {
				_, err = tx.Exec("SET LOCAL max_parallel_workers_per_gather = ?", MaxParallelWorkersPerGather)
			}
			if err != nil {
				return fmt.Errorf("error setting probes: %w", err)
			}
		case "hnsw":
			if dso.collection.IsIndexed {
				_, err = tx.Exec("SET LOCAL hnsw.ef_search = ?", DefaultEFSearch)
			} else {
				_, err = tx.Exec("SET LOCAL max_parallel_workers_per_gather = ?", MaxParallelWorkersPerGather)
			}
		default:
			return fmt.Errorf("unknown index type %s", dso.collection.IndexType)
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

	if dso.searchPayload.SearchType == models.SearchTypeMMR {
		results, err = dso.reRankMMR(results)
		if err != nil {
			return nil, fmt.Errorf("error reranking results: %w", err)
		}
	}

	resultPage := &models.DocumentSearchResultPage{
		Results:     searchResultsFromSearchQueries(results),
		QueryVector: dso.queryVector,
		ResultCount: count,
	}

	return resultPage, nil
}

// reRankMMR reranks the results using the MMR algorithm.
func (dso *documentSearchOperation) reRankMMR(
	results []models.SearchDocumentResult,
) ([]models.SearchDocumentResult, error) {
	lambda := dso.searchPayload.MMRLambda
	if lambda == 0 {
		lambda = DefaultMMRLambda
	}

	k := dso.limit
	if k == 0 {
		k = DefaultDocumentSearchLimit
	}

	resultVectors := make([][]float32, len(results))
	for i := range results {
		resultVectors[i] = results[i].Embedding
	}

	rankedIndices, err := search.MaximalMarginalRelevance(dso.queryVector, resultVectors, lambda, k)
	if err != nil {
		return nil, fmt.Errorf("error reranking results: %w", err)
	}

	rankedResults := make([]models.SearchDocumentResult, len(rankedIndices))
	for i := range rankedIndices {
		rankedResults[i] = results[rankedIndices[i]]
	}

	return rankedResults, nil
}

// execQuery executes the query and scans the results into the provided results slice. It accepts a bun DB or Tx.
func (dso *documentSearchOperation) execQuery(
	db bun.IDB,
	results *[]models.SearchDocumentResult,
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

	return count, nil
}

func (dso *documentSearchOperation) buildQuery(db bun.IDB) (*bun.SelectQuery, error) {
	m := &[]models.SearchDocumentResult{}
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
	if dso.searchPayload.SearchType == models.SearchTypeMMR {
		limit *= DefaultMMRMultiplier
		if limit < 10 {
			limit = 10
		}
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
		qb = parseJSONQuery(qb, &jq, false, "")
	}

	query = qb.Unwrap().(*bun.SelectQuery)

	return query, nil
}

func searchResultsFromSearchQueries(s []models.SearchDocumentResult) []models.DocumentSearchResult {
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
