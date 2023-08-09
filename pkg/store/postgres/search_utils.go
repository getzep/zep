package postgres

import (
	"strings"

	"github.com/uptrace/bun"
)

// TODO: refactor to a single function used across both document and memory search

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

// parseDocumentJSONQuery recursively parses a JSONQuery and returns a bun.QueryBuilder.
// TODO: fix the addition of extraneous parentheses in the query
func parseDocumentJSONQuery(qb bun.QueryBuilder, jq *JSONQuery, isOr bool) bun.QueryBuilder {
	if jq.JSONPath != "" {
		path := strings.ReplaceAll(jq.JSONPath, "'", "\"")
		if isOr {
			qb = qb.WhereOr(
				"jsonb_path_exists(metadata, ?)",
				path,
			)
		} else {
			qb = qb.Where(
				"jsonb_path_exists(metadata, ?)",
				path,
			)
		}
	}

	if len(jq.And) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.And {
				qq = parseDocumentJSONQuery(qq, subQuery, false)
			}
			return qq
		})
	}

	if len(jq.Or) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.Or {
				qq = parseDocumentJSONQuery(qq, subQuery, true)
			}
			return qq
		})
	}

	return qb
}
