package postgres

import (
	"testing"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestGetPerpetualMemory(t *testing.T) {
	// Create a session
	sessionID := testutils.GenerateRandomString(10)

	// Add one by one to force summarizer to run multiple times
	for _, m := range testutils.TestMessages {
		err := appState.MemoryStore.PutMemory(testCtx, appState, sessionID,
			&models.Memory{Messages: []models.Message{m}},
			false)
		assert.NoError(t, err, "putMessages should not return an error")
	}

	// Wait for messages to be processed
	timeout := time.After(30 * time.Second)
	tick := time.Tick(500 * time.Millisecond)
	for {
		select {
		case <-timeout:
			t.Fatal("timed out waiting for messages to be indexed")
		case <-tick:
			me, err := getMessageEmbeddings(testCtx, testDB, sessionID)
			assert.NoError(t, err, "getMessageEmbeddings should not return an error")
			se, err := getSummaryEmbeddings(testCtx, testDB, sessionID)
			assert.NoError(t, err, "getSummaryEmbeddings should not return an error")
			t.Logf(
				"Waiting for messages to be indexed: %d/%d messages, %d summaries",
				len(me),
				len(testutils.TestMessages),
				len(se),
			)
			if len(me) == len(testutils.TestMessages) && len(se) >= 2 {
				goto DONE
			}
		}
	}

DONE:

	testCases := []struct {
		name                     string
		lastNMessages            int
		includeCurrentSummary    bool
		maxPerpetualSummaryCount int
	}{
		{"LastNMessages 2, IncludeCurrentSummary true, MaxPerpetualSummaryCount 1", 2, true, 1},
		{"LastNMessages 2, IncludeCurrentSummary false, MaxPerpetualSummaryCount 1", 2, false, 1},
		{"LastNMessages 5, IncludeCurrentSummary true, MaxPerpetualSummaryCount 1", 5, true, 1},
		{"LastNMessages 5, IncludeCurrentSummary false, MaxPerpetualSummaryCount 2", 5, false, 2},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			config := &models.MemoryConfig{
				SessionID:                sessionID,
				Type:                     models.PerpetualMemoryType,
				LastNMessages:            tc.lastNMessages,
				IncludeCurrentSummary:    tc.includeCurrentSummary,
				MaxPerpetualSummaryCount: tc.maxPerpetualSummaryCount,
				UseMMR:                   true,
			}

			memory, err := getPerpetualMemory(testCtx, testDB, appState, config)
			assert.NoError(t, err)
			assert.NotNil(t, memory)

			// Check the current summary
			if tc.includeCurrentSummary {
				assert.NotNil(t, memory.Summary)
				assert.NotEmpty(t, memory.Summary.Content)
			} else {
				assert.Nil(t, memory.Summary)
			}

			// Check the messages
			assert.Equal(t, tc.lastNMessages, len(memory.Messages))

			// Check the summary count
			assert.Equal(t, tc.maxPerpetualSummaryCount, len(memory.Summaries))
		})
	}
}
