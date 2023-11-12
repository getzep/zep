package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/search"
	"github.com/getzep/zep/pkg/store"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

const DefaultMemorySearchLimit = 10

type JSONQuery struct {
	JSONPath string       `json:"jsonpath"`
	And      []*JSONQuery `json:"and,omitempty"`
	Or       []*JSONQuery `json:"or,omitempty"`
}

func searchMemory(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	sessionID string,
	query *models.MemorySearchPayload,
	limit int,
) ([]models.MemorySearchResult, error) {
	if query == nil || appState == nil {
		return nil, store.NewStorageError("nil query or appState received", nil)
	}

	if query.Text == "" && len(query.Metadata) == 0 {
		return nil, errors.New("empty query")
	}

	var dbQuery *bun.SelectQuery
	var tablePrefix string

	switch query.SearchScope {
	case models.SearchScopeMessages, "":
		dbQuery = buildMessageSearchQuery(ctx, db, query)
		tablePrefix = "m"
	case models.SearchScopeSummary:
		dbQuery = buildSummarySearchQuery(ctx, db, query)
		tablePrefix = "s"
	default:
		return nil, errors.New("invalid search scope")
	}

	var err error
	var queryEmbedding []float32
	if query.Text != "" {
		dbQuery, queryEmbedding, err = addMemoryVectorColumn(ctx, appState, dbQuery, query.Text)
		if err != nil {
			return nil, store.NewStorageError("error adding vector column", err)
		}
	}
	if len(query.Metadata) > 0 {
		var err error
		dbQuery, err = applyMemoryMetadataFilter(dbQuery, query.Metadata, tablePrefix)
		if err != nil {
			return nil, store.NewStorageError("error applying metadata filter", err)
		}
	}

	dbQuery = dbQuery.Where("?.session_id = ?", bun.Safe(tablePrefix), sessionID)

	// Ensure we don't return deleted records.
	dbQuery = dbQuery.Where("?.deleted_at IS NULL", bun.Safe(tablePrefix))

	// Add sort and limit.
	addMessagesSortQuery(query.Text, dbQuery, tablePrefix)

	if limit == 0 {
		limit = DefaultMemorySearchLimit
	}

	// If we're using MMR, we need to return more results than the limit so we can
	// rerank them.
	if query.SearchType == models.SearchTypeMMR {
		if query.MMRLambda == 0 {
			query.MMRLambda = DefaultMMRLambda
		}
		tmpLimit := limit * DefaultMMRMultiplier
		if tmpLimit < 10 {
			tmpLimit = 10
		}
		dbQuery = dbQuery.Limit(tmpLimit)
	} else {
		dbQuery = dbQuery.Limit(limit)
	}

	results, err := executeMessagesSearchScan(ctx, dbQuery)
	if err != nil {
		return nil, store.NewStorageError("memory searchMemory failed", err)
	}

	// If we didn't find any results, return early.
	if len(results) == 0 {
		return []models.MemorySearchResult{}, nil
	}

	filteredResults := filterValidMessageSearchResults(results, query.Metadata)

	// If we're using MMR, rerank the results.
	if query.SearchType == models.SearchTypeMMR {
		filteredResults, err = rerankMMR(filteredResults, queryEmbedding, query.MMRLambda, limit)
		if err != nil {
			return nil, store.NewStorageError("error applying mmr", err)
		}
	}

	return filteredResults, nil
}

// rerankMMR reranks the results using the Maximal Marginal Relevance algorithm
func rerankMMR(
	results []models.MemorySearchResult,
	queryEmbedding []float32,
	lambda float32,
	limit int,
) ([]models.MemorySearchResult, error) {
	embeddingList := make([][]float32, len(results))
	for i, result := range results {
		embeddingList[i] = result.Embedding
	}
	rerankedIdxs, err := search.MaximalMarginalRelevance(
		queryEmbedding,
		embeddingList,
		lambda,
		limit,
	)
	if err != nil {
		return nil, store.NewStorageError("error applying mmr", err)
	}
	rerankedResults := make([]models.MemorySearchResult, len(rerankedIdxs))
	for i, idx := range rerankedIdxs {
		rerankedResults[i] = results[idx]
	}
	return rerankedResults, nil
}

func buildMessageSearchQuery(
	_ context.Context,
	db *bun.DB,
	query *models.MemorySearchPayload,
) *bun.SelectQuery {
	dbQuery := db.NewSelect().TableExpr("message_embedding AS me").
		Join("JOIN message AS m").
		JoinOn("me.message_uuid = m.uuid").
		ColumnExpr("m.uuid AS message__uuid").
		ColumnExpr("m.created_at AS message__created_at").
		ColumnExpr("m.role AS message__role").
		ColumnExpr("m.content AS message__content").
		ColumnExpr("m.metadata AS message__metadata").
		ColumnExpr("m.token_count AS message__token_count")

	if query.SearchType == models.SearchTypeMMR {
		dbQuery = dbQuery.ColumnExpr("me.embedding AS embedding")
	}

	return dbQuery
}

func buildSummarySearchQuery(
	_ context.Context,
	db *bun.DB,
	query *models.MemorySearchPayload,
) *bun.SelectQuery {
	dbQuery := db.NewSelect().TableExpr("summary_embedding AS se").
		Join("JOIN summary AS s").
		JoinOn("se.summary_uuid = s.uuid").
		ColumnExpr("s.uuid AS summary__uuid").
		ColumnExpr("s.created_at AS summary__created_at").
		ColumnExpr("s.content AS summary__content").
		ColumnExpr("s.metadata AS summary__metadata").
		ColumnExpr("s.token_count AS summary__token_count")

	if query.SearchType == models.SearchTypeMMR {
		dbQuery = dbQuery.ColumnExpr("se.embedding AS embedding")
	}

	return dbQuery
}

func applyMemoryMetadataFilter(
	dbQuery *bun.SelectQuery,
	metadata map[string]any,
	tablePrefix string,
) (*bun.SelectQuery, error) {
	qb := dbQuery.QueryBuilder()

	if where, ok := metadata["where"]; ok {
		j, err := json.Marshal(where)
		if err != nil {
			return nil, store.NewStorageError("error marshalling metadata", err)
		}

		var jq JSONQuery
		err = json.Unmarshal(j, &jq)
		if err != nil {
			return nil, store.NewStorageError("error unmarshalling metadata", err)
		}
		qb = parseJSONQuery(qb, &jq, false, tablePrefix)
	}

	addMessageDateFilters(&qb, metadata, tablePrefix)

	dbQuery = qb.Unwrap().(*bun.SelectQuery)

	return dbQuery, nil
}

func addMessagesSortQuery(searchText string, dbQuery *bun.SelectQuery, tablePrefix string) {
	if searchText != "" {
		dbQuery.Order("dist DESC")
	} else {
		dbQuery.Order(tablePrefix + ".created_at DESC")
	}
}

func executeMessagesSearchScan(
	ctx context.Context,
	dbQuery *bun.SelectQuery,
) ([]models.MemorySearchResult, error) {
	var results []models.MemorySearchResult
	if err := dbQuery.Scan(ctx, &results); err != nil {
		return nil, fmt.Errorf("error scanning: %w", err)
	}
	if len(results) == 0 {
		return []models.MemorySearchResult{}, nil
	}
	return results, nil
}

func filterValidMessageSearchResults(
	results []models.MemorySearchResult,
	metadata map[string]interface{},
) []models.MemorySearchResult {
	var filteredResults []models.MemorySearchResult
	for _, result := range results {
		if !math.IsNaN(result.Dist) || len(metadata) > 0 {
			filteredResults = append(filteredResults, result)
		}
	}
	return filteredResults
}

// addMessageDateFilters adds date filters to the query
func addMessageDateFilters(qb *bun.QueryBuilder, m map[string]any, tablePrefix string) {
	if startDate, ok := m["start_date"]; ok {
		*qb = (*qb).Where("?.created_at >= ?", bun.Safe(tablePrefix), startDate)
	}
	if endDate, ok := m["end_date"]; ok {
		*qb = (*qb).Where("?.created_at <= ?", bun.Safe(tablePrefix), endDate)
	}
}

// addMemoryVectorColumn adds a column to the query that calculates the distance between the query text and the message embedding
func addMemoryVectorColumn(
	ctx context.Context,
	appState *models.AppState,
	q *bun.SelectQuery,
	queryText string,
) (*bun.SelectQuery, []float32, error) {
	documentType := "message"
	model, err := llms.GetEmbeddingModel(appState, documentType)
	if err != nil {
		return nil, nil, store.NewStorageError("failed to get message embedding model", err)
	}

	e, err := llms.EmbedTexts(ctx, appState, model, documentType, []string{queryText})
	if err != nil {
		return nil, nil, store.NewStorageError("failed to embed query", err)
	}

	vector := pgvector.NewVector(e[0])
	return q.ColumnExpr("(embedding <#> ?) * -1 AS dist", vector), e[0], nil
}
