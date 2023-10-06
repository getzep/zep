package postgres

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
	"github.com/uptrace/bun"
)

func TestParseJSONQuery(t *testing.T) {
	tests := []struct {
		name         string
		jsonQuery    string
		expectedCond string
		tablePrefix  string
	}{
		{
			name:         "Test 1",
			jsonQuery:    `{"where": {"jsonpath": "$.system.entities[*] ? (@.Label == \"DATE\")"}}`,
			expectedCond: `WHERE (jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == "DATE")'))`,
			tablePrefix:  "m",
		},
		{
			name:         "Without Prefix",
			jsonQuery:    `{"where": {"or": [{"jsonpath": "$.system.entities[*] ? (@.Label == \"DATE\")"},{"jsonpath": "$.system.entities[*] ? (@.Label == \"ORG\")"}]}}`,
			expectedCond: `WHERE ((jsonb_path_exists(metadata, '$.system.entities[*] ? (@.Label == "DATE")')) OR (jsonb_path_exists(metadata, '$.system.entities[*] ? (@.Label == "ORG")')))`,
			tablePrefix:  "",
		},
		{
			name:         "Test 3",
			jsonQuery:    `{"where": {"and": [{"jsonpath": "$.system.entities[*] ? (@.Label == \"DATE\")"},{"jsonpath": "$.system.entities[*] ? (@.Label == \"ORG\")"},{"or": [{"jsonpath": "$.system.entities[*] ? (@.Name == \"Iceland\")"},{"jsonpath": "$.system.entities[*] ? (@.Name == \"Canada\")"}]}]}}`,
			expectedCond: `WHERE ((jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == "DATE")')) AND (jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == "ORG")')) AND ((jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Name == "Iceland")')) OR (jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Name == "Canada")'))))`,
			tablePrefix:  "m",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			qb := testDB.NewSelect().
				Model(&[]models.MemorySearchResult{}).
				QueryBuilder()

			var metadata map[string]interface{}
			err := json.Unmarshal([]byte(tt.jsonQuery), &metadata)
			assert.NoError(t, err)

			query, err := json.Marshal(metadata["where"])
			assert.NoError(t, err)

			var jsonQuery JSONQuery
			err = json.Unmarshal(query, &jsonQuery)
			assert.NoError(t, err)

			qb = parseJSONQuery(qb, &jsonQuery, false, tt.tablePrefix)

			selectQuery := qb.Unwrap().(*bun.SelectQuery)

			// Extract the WHERE conditions from the SQL query
			sql := selectQuery.String()
			whereIndex := strings.Index(sql, "WHERE")
			assert.True(t, whereIndex > 0, "WHERE clause should be present")
			cond := sql[whereIndex:]

			// We use assert.Equal to test if the conditions are built correctly.
			assert.Equal(t, tt.expectedCond, cond)
		})
	}
}
