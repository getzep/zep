package memorystore

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strings"

	"github.com/sirupsen/logrus"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

const defaultSearchLimit = 10

type JSONQuery struct {
	JSONPath string       `json:"jsonpath"`
	And      []*JSONQuery `json:"and,omitempty"`
	Or       []*JSONQuery `json:"or,omitempty"`
}

func searchMessages(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	sessionID string,
	query *models.MemorySearchPayload,
	limit int,
) ([]models.MemorySearchResult, error) {
	logrus.Debugf("searchMessages called for session %s", sessionID)

	if query == nil || appState == nil {
		return nil, NewStorageError("nil query or appState received", nil)
	}

	if query.Text == "" && len(query.Metadata) == 0 {
		return nil, NewStorageError("empty query", errors.New("empty query"))
	}

	if limit == 0 {
		limit = defaultSearchLimit
	}

	dbQuery := buildDBSelectQuery(ctx, appState, db, query)
	if len(query.Metadata) > 0 {
		var err error
		dbQuery, err = applyMetadataFilter(dbQuery, query.Metadata)
		if err != nil {
			return nil, NewStorageError("error applying metadata filter", err)
		}
	}

	sortQuery(query.Text, dbQuery)
	dbQuery = dbQuery.Limit(limit)

	results, err := executeScan(ctx, dbQuery)
	if err != nil {
		return nil, NewStorageError("memory searchMessages failed", err)
	}

	filteredResults := filterValidResults(results, query.Metadata)
	logrus.Debugf("searchMessages completed for session %s", sessionID)

	return filteredResults, nil
}

func buildDBSelectQuery(
	ctx context.Context,
	appState *models.AppState,
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

	if query.Text != "" {
		dbQuery, _ = addVectorColumn(ctx, appState, dbQuery, query.Text)
	}

	return dbQuery
}

func applyMetadataFilter(
	dbQuery *bun.SelectQuery,
	metadata map[string]interface{},
) (*bun.SelectQuery, error) {
	qb := dbQuery.QueryBuilder()

	if where, ok := metadata["where"]; ok {
		j, err := json.Marshal(where)
		if err != nil {
			return nil, NewStorageError("error marshalling metadata", err)
		}

		var jq JSONQuery
		err = json.Unmarshal(j, &jq)
		if err != nil {
			return nil, NewStorageError("error unmarshalling metadata", err)
		}
		qb = parseJSONQuery(qb, &jq, false)
	}

	addDateFilters(&qb, metadata)

	dbQuery = qb.Unwrap().(*bun.SelectQuery)

	return dbQuery, nil
}

func sortQuery(searchText string, dbQuery *bun.SelectQuery) {
	if searchText != "" {
		dbQuery.Order("dist DESC")
	} else {
		dbQuery.Order("m.created_at DESC")
	}
}

func executeScan(
	ctx context.Context,
	dbQuery *bun.SelectQuery,
) ([]models.MemorySearchResult, error) {
	var results []models.MemorySearchResult
	err := dbQuery.Scan(ctx, &results)
	return results, err
}

func filterValidResults(
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

// addDateFilters adds date filters to the query
func addDateFilters(qb *bun.QueryBuilder, m map[string]interface{}) {
	if startDate, ok := m["start_date"]; ok {
		*qb = (*qb).Where("m.created_at >= ?", startDate)
	}
	if endDate, ok := m["end_date"]; ok {
		*qb = (*qb).Where("m.created_at <= ?", endDate)
	}
}

// addVectorColumn adds a column to the query that calculates the distance between the query text and the message embedding
func addVectorColumn(
	ctx context.Context,
	appState *models.AppState,
	q *bun.SelectQuery,
	queryText string,
) (*bun.SelectQuery, error) {
	e, err := llms.EmbedMessages(ctx, appState, []string{queryText})
	if err != nil {
		return nil, NewStorageError("failed to embed query", err)
	}

	vector := pgvector.NewVector(e[0].Embedding)
	return q.ColumnExpr("1 - (embedding <=> ? ) AS dist", vector), nil
}

// parseJSONQuery recursively parses a JSONQuery and returns a bun.QueryBuilder.
// TODO: fix the addition of extraneous parentheses in the query
func parseJSONQuery(qb bun.QueryBuilder, jq *JSONQuery, isOr bool) bun.QueryBuilder {
	if jq.JSONPath != "" {
		path := strings.ReplaceAll(jq.JSONPath, "'", "\"")
		if isOr {
			qb = qb.WhereOr(
				"jsonb_path_exists(m.metadata, ?)",
				path,
			)
		} else {
			qb = qb.Where(
				"jsonb_path_exists(m.metadata, ?)",
				path,
			)
		}
	}

	if len(jq.And) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.And {
				qq = parseJSONQuery(qq, subQuery, false)
			}
			return qq
		})
	}

	if len(jq.Or) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.Or {
				qq = parseJSONQuery(qq, subQuery, true)
			}
			return qq
		})
	}

	return qb
}
