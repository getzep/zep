package memorystore

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

func searchMessages(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	sessionID string,
	query *models.SearchPayload,
	limit int,
) ([]models.SearchResult, error) {
	var err error
	log.Debugf("searchMessages called for session %s", sessionID)

	if query == nil {
		return nil, NewStorageError("nil query received", nil)
	}

	s := query.Text
	m := query.Metadata
	if s == "" && len(m) == 0 {
		return nil, NewStorageError("empty query", errors.New("empty query"))
	}

	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	if limit == 0 {
		limit = 10
	}

	var jsonPathQuery string
	if len(m) > 0 {
		jsonPathQuery = jsonPathFromMap(m, "$")
	}

	dbQuery := db.NewSelect().TableExpr("message_embedding AS me").
		Join("JOIN message AS m").
		JoinOn("me.message_uuid = m.uuid").
		ColumnExpr("m.uuid AS message__uuid").
		ColumnExpr("m.created_at AS message__created_at").
		ColumnExpr("m.role AS message__role").
		ColumnExpr("m.content AS message__content").
		ColumnExpr("m.metadata AS message__metadata").
		ColumnExpr("m.token_count AS message__token_count")

	// if we have a query text, add the vector column
	if s != "" {
		dbQuery, err = addVectorColumn(ctx, appState, dbQuery, s)
		if err != nil {
			return nil, NewStorageError("error adding vector column", err)
		}
	}

	if jsonPathQuery != "" {
		dbQuery = dbQuery.Where("jsonb_path_exists(m.metadata, ?)", jsonPathQuery)
	}

	dbQuery = dbQuery.Where("m.session_id = ?", sessionID).
		Where("m.session_id = ?", sessionID)

	if s != "" {
		dbQuery = dbQuery.Order("dist DESC")
	} else {
		dbQuery = dbQuery.Order("m.created_at DESC")
	}

	dbQuery = dbQuery.Limit(limit)

	var results []models.SearchResult
	err = dbQuery.Scan(ctx, &results)
	if err != nil {
		return nil, NewStorageError("memory searchMessages failed", err)
	}

	// some results may be returned where distance is NaN. This is a race between
	// newly added messages and the text query. We filter these out, but only
	// if we're searching solely on search text.
	var filteredResults []models.SearchResult
	for _, result := range results {
		if !math.IsNaN(result.Dist) || len(m) > 0 {
			filteredResults = append(filteredResults, result)
		}
	}
	log.Debugf("searchMessages completed for session %s", sessionID)

	return filteredResults, nil
}

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

type JSONQuery struct {
	Jsonpath string       `json:"jsonpath"`
	And      []*JSONQuery `json:"and"`
	Or       []*JSONQuery `json:"or"`
}

func parseQuery(qb bun.QueryBuilder, jq *JSONQuery) bun.QueryBuilder {
	if jq.Jsonpath != "" {
		qb = qb.Where("jsonb_path_exists(m.metadata, ?)", jq.Jsonpath)
	}

	if len(jq.And) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.And {
				qq = parseQuery(qq, subQuery)
			}
			return qq
		})
	}

	if len(jq.Or) > 0 {
		qb = qb.WhereGroup(" OR ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.Or {
				qq = parseQuery(qq, subQuery)
			}
			return qq
		})
	}

	return qb
}

func addWhere(qb bun.QueryBuilder, jq *JSONQuery) bun.QueryBuilder {
	return parseQuery(qb, jq)
}

// jsonPathFromMap generates a JSONPath expression from a metadata map.
// path is the current path in the map, and is used for recursion. Start with
// path = "$" when calling from outside the function.
func jsonPathFromMap(m map[string]interface{}, path string) string {
	expressions := []string{}

	for key, value := range m {
		newPath := path
		if newPath != "@" {
			newPath += "." + key
		}

		switch v := value.(type) {
		case map[string]interface{}:
			// Recursively generate JSONPath for nested map
			expressions = append(expressions, jsonPathFromMap(v, newPath))
		case []interface{}:
			// Generate JSONPath for each item in array
			for _, item := range v {
				if itemMap, ok := item.(map[string]interface{}); ok {
					itemExpression := jsonPathFromMap(itemMap, "")
					expressions = append(expressions, fmt.Sprintf("%s[*] ? (%s)", newPath, itemExpression))
				}
			}
		default:
			// Generate JSONPath for simple key-value pair
			// Add cases for numeric types
			switch v := value.(type) {
			case int, float64:
				expressions = append(expressions, fmt.Sprintf("@%s == %v", newPath, v))
			default:
				strValue := fmt.Sprintf("%v", v)
				strValue = strings.ReplaceAll(strValue, "\"", "\\\"")
				expressions = append(expressions, fmt.Sprintf("@%s == \"%v\"", newPath, strValue))
			}
		}
	}

	return strings.Join(expressions, " && ")
}
