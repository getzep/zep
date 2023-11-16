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
			if len(me) == len(testutils.TestMessages) && len(se) >= 1 {
				goto DONE
			}
		}
	}

DONE:

	testCases := []struct {
		name          string
		lastNMessages int
	}{
		{"LastNMessages 2", 2},
		{"LastNMessages 5", 5},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			config := &models.MemoryConfig{
				SessionID:     sessionID,
				Type:          models.PerpetualMemoryType,
				LastNMessages: tc.lastNMessages,
			}

			memory, err := getPerpetualMemory(testCtx, testDB, appState, config)
			assert.NoError(t, err)
			assert.NotNil(t, memory)

			// Check the messages
			assert.Equal(t, tc.lastNMessages, len(memory.Messages))

			// Check the summary is present
			assert.NotEmpty(t, memory.Summary.Content)
		})
	}
}
