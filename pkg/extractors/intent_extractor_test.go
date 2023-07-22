package extractors

import (
	"testing"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestIntentExtractor_Extract(t *testing.T) {
	store := appState.MemoryStore

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	testMessages := testutils.TestMessages[:5]

	err = store.PutMemory(
		testCtx,
		appState,
		sessionID,
		&models.Memory{Messages: testMessages},
		true,
	)
	assert.NoError(t, err)

	// Sleep for 2 seconds
	time.Sleep(2 * time.Second)

	memories, err := store.GetMemory(testCtx, appState, sessionID, 0)
	assert.NoError(t, err)
	assert.Equal(t, len(testMessages), len(memories.Messages))

	intentExtractor := NewIntentExtractor()

	for i, message := range memories.Messages {
		singleMessageEvent := &models.MessageEvent{
			SessionID: sessionID,
			Messages:  []models.Message{message},
		}

		err = intentExtractor.Extract(testCtx, appState, singleMessageEvent)
		assert.NoError(t, err)

		metadata := message.Metadata["system"]
		if metadata != nil {
			if metadataMap, ok := metadata.(map[string]interface{}); ok {
				assert.NotNil(t, metadataMap["intent"])
			}
		}

		// Validate the message metadata after the extraction
		assert.Equal(t, memories.Messages[i].Metadata, message.Metadata)

		// Sleep for 2 seconds between each extract
		time.Sleep(2 * time.Second)
	}
}
