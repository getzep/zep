package postgres

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/uptrace/bun"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestMemorySearch(t *testing.T) {
	// Test data
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	// Call putMessages function
	err = appState.MemoryStore.PutMemory(testCtx, sessionID,
		&models.Memory{
			Messages: testutils.TestMessages,
		}, false,
	)
	assert.NoError(t, err, "PutMemory should not return an error")

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err, "NewMessageDAO should not return an error")
	summaryDAO, err := NewSummaryDAO(testDB, appState, sessionID)
	assert.NoError(t, err, "NewSummaryDAO should not return an error")

	timeout := time.After(10 * time.Second)
	tick := time.Tick(500 * time.Millisecond)
	for {
		select {
		case <-timeout:
			t.Fatal("timed out waiting for messages to be indexed")
		case <-tick:
			me, err := messageDAO.GetEmbeddingListBySession(testCtx)
			assert.NoError(t, err, "GetEmbeddingListBySession should not return an error")
			se, err := summaryDAO.GetEmbeddings(testCtx)
			assert.NoError(t, err, "GetEmbeddings should not return an error")
			if len(me) != 0 && len(se) != 0 {
				goto DONE
			}
		}
	}

DONE:
	// Test cases
	testCases := []struct {
		name              string
		query             string
		limit             int
		expectedErrorText string
		SearchScope       models.SearchScope
		searchType        models.SearchType
	}{
		{"Empty Query", "", 0, "empty query",
			models.SearchScopeMessages, models.SearchTypeSimilarity},
		{
			"Non-empty Query",
			"travel",
			0,
			"",
			models.SearchScopeMessages,
			models.SearchTypeSimilarity,
		},
		{"Limit 0", "travel", 0, "", models.SearchScopeMessages, models.SearchTypeSimilarity},
		{"Limit 5", "travel", 5, "", models.SearchScopeMessages, models.SearchTypeSimilarity},
		{"Limit 5 Empty SearchScope", "travel", 5, "", "", models.SearchTypeSimilarity},
		{"MMR Query", "travel", 5, "", models.SearchScopeMessages, models.SearchTypeMMR},
		{
			"SearchScope Summary",
			"travel",
			1,
			"",
			models.SearchScopeSummary,
			models.SearchTypeSimilarity,
		},
		{
			"SearchScope Summary MMR",
			"travel",
			1,
			"",
			models.SearchScopeSummary,
			models.SearchTypeMMR,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			q := models.MemorySearchPayload{
				Text:        tc.query,
				SearchType:  tc.searchType,
				SearchScope: tc.SearchScope,
			}
			expectedLastN := tc.limit
			if expectedLastN == 0 {
				expectedLastN = 10 // Default value
			}

			s, err := searchMemory(testCtx, appState, testDB, sessionID, &q, expectedLastN)

			if tc.expectedErrorText != "" {
				assert.ErrorContains(
					t,
					err,
					tc.expectedErrorText,
					"searchMemory should return the expected error",
				)
			} else {
				assert.NoError(t, err, "searchMemory should not return an error")
				assert.Len(t, s, expectedLastN, fmt.Sprintf("Expected %d messages to be returned", expectedLastN))

				if tc.SearchScope == models.SearchScopeSummary {
					for _, res := range s {
						assert.NotNil(t, res.Summary, "summary should be present")
						assert.NotNil(t, res.Summary.UUID, "summary__uuid should be present")
						assert.NotNil(t, res.Summary.CreatedAt, "summary__created_at should be present")
						assert.NotNil(t, res.Summary.Content, "summary__content should be present")
						assert.NotNil(t, res.Dist, "dist should be present")
					}
				} else {
					for _, res := range s {
						assert.NotNil(t, res.Message.UUID, "message__uuid should be present")
						assert.NotNil(t, res.Message.CreatedAt, "message__created_at should be present")
						assert.NotNil(t, res.Message.Role, "message__role should be present")
						assert.NotNil(t, res.Message.Content, "message__content should be present")
						assert.NotNil(t, res.Dist, "dist should be present")
					}
				}
			}
		})
	}
}

func TestAddDateFilters(t *testing.T) {
	tests := []struct {
		name         string
		inputDates   string
		expectedCond string
	}{
		{
			name:         "Test 1 - Start Date only",
			inputDates:   `{"start_date": "2022-01-01"}`,
			expectedCond: `WHERE (m.created_at >= '2022-01-01')`,
		},
		{
			name:         "Test 2 - End Date only",
			inputDates:   `{"end_date": "2022-01-31"}`,
			expectedCond: `WHERE (m.created_at <= '2022-01-31')`,
		},
		{
			name:         "Test 3 - Start and End Dates",
			inputDates:   `{"start_date": "2022-01-01", "end_date": "2022-01-31"}`,
			expectedCond: `WHERE (m.created_at >= '2022-01-01') AND (m.created_at <= '2022-01-31')`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			qb := testDB.NewSelect().
				Model(&[]models.MemorySearchResult{}).
				QueryBuilder()

			var inputDates map[string]interface{}
			err := json.Unmarshal([]byte(tt.inputDates), &inputDates)
			assert.NoError(t, err)

			addMessageDateFilters(&qb, inputDates, "m")

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
