package extractors

import (
	"context"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/memorystore"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestIntentExtractor_Extract(t *testing.T) {
	ctx := context.Background()

	db := memorystore.NewPostgresConn(testutils.GetDSN())
	memorystore.CleanDB(t, db)

	cfg := testutils.NewTestConfig()

	appState := &models.AppState{Config: cfg}
	store, err := memorystore.NewPostgresMemoryStore(appState, db)
	assert.NoError(t, err)
	appState.MemoryStore = store
	appState.OpenAIClient = llms.NewOpenAIRetryClient(cfg)

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	testMessages := testutils.TestMessages[:5]

	err = store.PutMemory(ctx, appState, sessionID, &models.Memory{Messages: testMessages}, true)
	assert.NoError(t, err)

	// Sleep for 2 seconds
	time.Sleep(2 * time.Second)

	memories, err := store.GetMemory(ctx, appState, sessionID, 0)
	assert.NoError(t, err)
	assert.Equal(t, len(testMessages), len(memories.Messages))

	intentExtractor := NewIntentExtractor()

	for i, message := range memories.Messages {
		singleMessageEvent := &models.MessageEvent{
			SessionID: sessionID,
			Messages:  []models.Message{message},
		}

		err = intentExtractor.Extract(ctx, appState, singleMessageEvent)
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
