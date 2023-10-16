package postgres

import (
	"fmt"
	"strings"

	"github.com/uptrace/bun"
)

const DefaultMMRMultiplier = 2
const DefaultMMRLambda = 0.5

// parseJSONQuery recursively parses a JSONQuery and returns a bun.QueryBuilder.
// TODO: fix the addition of extraneous parentheses in the query
func parseJSONQuery(
	qb bun.QueryBuilder,
	jq *JSONQuery,
	isOr bool,
	tablePrefix string,
) bun.QueryBuilder {
	var tp string
	if tablePrefix != "" {
		tp = tablePrefix + "."
	}
	if jq.JSONPath != "" {
		path := strings.ReplaceAll(jq.JSONPath, "'", "\"")
		if isOr {
			qb = qb.WhereOr(
				fmt.Sprintf("jsonb_path_exists(%smetadata, ?)", tp),
				path,
			)
		} else {
			qb = qb.Where(
				fmt.Sprintf("jsonb_path_exists(%smetadata, ?)", tp),
				path,
			)
		}
	}

	if len(jq.And) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.And {
				qq = parseJSONQuery(qq, subQuery, false, tablePrefix)
			}
			return qq
		})
	}

	if len(jq.Or) > 0 {
		qb = qb.WhereGroup(" AND ", func(qq bun.QueryBuilder) bun.QueryBuilder {
			for _, subQuery := range jq.Or {
				qq = parseJSONQuery(qq, subQuery, true, tablePrefix)
			}
			return qq
		})
	}

	return qb
}

func getAscDesc(asc bool) string {
	if asc {
		return "ASC"
	}
	return "DESC"
}
