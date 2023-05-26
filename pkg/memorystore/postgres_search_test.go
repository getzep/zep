package memorystore

import (
	"encoding/json"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/getzep/zep/pkg/models"

	"github.com/stretchr/testify/assert"
	"github.com/uptrace/bun"
)

func TestAddWhere(t *testing.T) {
	tests := []struct {
		name         string
		jsonQuery    string
		expectedCond string
	}{
		{
			name:         "Test 1",
			jsonQuery:    `{"where": {"jsonpath": "$.system.entities[*] ? (@.Label == 'DATE')"}}`,
			expectedCond: `WHERE jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == ''DATE'')')`,
		},
		{
			name:         "Test 2",
			jsonQuery:    `{"where": {"or": [{"jsonpath": "$.system.entities[*] ? (@.Label == 'DATE')"},{"jsonpath": "$.system.entities[*] ? (@.Label == 'ORG')"}]}}`,
			expectedCond: `WHERE (jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == ''DATE'')') OR jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == ''ORG'')'))`,
		},
		{
			name:         "Test 3",
			jsonQuery:    `{"where": {"and": [{"jsonpath": "$.system.entities[*] ? (@.Label == 'DATE')"},{"jsonpath": "$.system.entities[*] ? (@.Label == 'ORG')"},{"or": [{"jsonpath": "$.system.entities[*] ? (@.Name == 'Iceland')"},{"jsonpath": "$.system.entities[*] ? (@.Name == 'Canada')"}]}]}}`,
			expectedCond: `WHERE (jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == ''DATE'')') AND jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Label == ''ORG'')') AND (jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Name == ''Iceland'')') OR jsonb_path_exists(m.metadata, '$.system.entities[*] ? (@.Name == ''Canada'')'))`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			qb := testDB.NewSelect().
				Model(&[]models.SearchResult{}).
				QueryBuilder()

			var jsonQuery JSONQuery
			err := json.Unmarshal([]byte(tt.jsonQuery), &jsonQuery)
			assert.NoError(t, err)

			qb = addWhere(qb, &jsonQuery)

			selectQuery := qb.Unwrap().(*bun.SelectQuery)

			// Extract the WHERE conditions from the SQL query
			sql, _, _ := selectQuery.ToSQL()
			cond := sql[strings.Index(sql, "WHERE"):]

			// We use assert.Equal to test if the conditions are built correctly.
			assert.Equal(t, tt.expectedCond, cond)
		})
	}
}

func TestJsonPathFromMap(t *testing.T) {
	tests := []struct {
		name     string
		input    map[string]interface{}
		expected []string
	}{
		{
			name: "Simple Map",
			input: map[string]interface{}{
				"Name": "John Doe",
				"Age":  30,
			},
			expected: []string{"@$.Name == \"John Doe\"", "@$.Age == 30"},
		},
		{
			name: "Nested Map",
			input: map[string]interface{}{
				"Name": "John Doe",
				"Details": map[string]interface{}{
					"Age":        30,
					"Occupation": "Engineer",
				},
			},
			expected: []string{
				"@$.Name == \"John Doe\"",
				"@$.Details.Age == 30",
				"@$.Details.Occupation == \"Engineer\"",
			},
		},
		{
			name: "Array of Maps",
			input: map[string]interface{}{
				"Employees": []interface{}{
					map[string]interface{}{
						"Name": "John Doe",
						"Age":  30,
					},
					map[string]interface{}{
						"Name": "Jane Doe",
						"Age":  25,
					},
				},
			},
			expected: []string{
				"$.Employees[*] ? (@.Name == \"John Doe\" && @.Age == 30)",
				"$.Employees[*] ? (@.Name == \"Jane Doe\" && @.Age == 25)",
			},
		},
		{
			name: "Nested Array",
			input: map[string]interface{}{
				"system": map[string]interface{}{
					"entities": []interface{}{
						map[string]interface{}{
							"Label": "MONEY",
							"Name":  "Around $200-$300",
						},
					},
				},
			},
			expected: []string{
				"$.system.entities[*] ? (@.Label == \"MONEY\" && @.Name == \"Around $200-$300\")",
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := jsonPathFromMap(test.input, "$")
			actual := strings.Split(result, " && ")
			sort.Strings(actual)

			expected := make([]string, len(test.expected))
			copy(expected, test.expected)
			sort.Strings(expected)

			if !reflect.DeepEqual(actual, expected) {
				t.Errorf("Expected '%v' but got '%v'", expected, actual)
			}
		})
	}

}
