package postgres

import (
	"context"
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

func TestVectorSearch(t *testing.T) {
	// Test data
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	// Call putMessages function
	msgs, err := putMessages(testCtx, testDB, sessionID, testutils.TestMessages)
	assert.NoError(t, err, "putMessages should not return an error")

	appState.MemoryStore.NotifyExtractors(
		context.Background(),
		appState,
		&models.MessageEvent{SessionID: sessionID,
			Messages: msgs},
	)

	// enrichment runs async. Wait for it to finish
	// This is hacky but I'd prefer not to add a WaitGroup to the putMessages function just for testing purposes
	time.Sleep(time.Second * 2)

	// Test cases
	testCases := []struct {
		name              string
		query             string
		limit             int
		expectedErrorText string
	}{
		{"Empty Query", "", 0, "empty query"},
		{"Non-empty Query", "travel", 0, ""},
		{"Limit 0", "travel", 0, ""},
		{"Limit 5", "travel", 5, ""},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			q := models.MemorySearchPayload{Text: tc.query}
			expectedLastN := tc.limit
			if expectedLastN == 0 {
				expectedLastN = 10 // Default value
			}

			s, err := searchMessages(testCtx, appState, testDB, sessionID, &q, expectedLastN)

			if tc.expectedErrorText != "" {
				assert.ErrorContains(
					t,
					err,
					tc.expectedErrorText,
					"searchMessages should return the expected error",
				)
			} else {
				assert.NoError(t, err, "searchMessages should not return an error")
				assert.Len(t, s, expectedLastN, fmt.Sprintf("Expected %d messages to be returned", expectedLastN))

				for _, res := range s {
					assert.NotNil(t, res.Message.UUID, "message__uuid should be present")
					assert.NotNil(t, res.Message.CreatedAt, "message__created_at should be present")
					assert.NotNil(t, res.Message.Role, "message__role should be present")
					assert.NotNil(t, res.Message.Content, "message__content should be present")
					assert.NotZero(t, res.Message.TokenCount, "message_token_count should be present")
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

			addMessageDateFilters(&qb, inputDates)

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
