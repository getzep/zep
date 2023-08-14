package extractors

import (
	"testing"
	"time"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func runTestIntentExtractor(t *testing.T, testAppState *models.AppState) {
	store := testAppState.MemoryStore

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	testMessages := testutils.TestMessages[:5]

	err = store.PutMemory(
		testCtx,
		testAppState,
		sessionID,
		&models.Memory{Messages: testMessages},
		true,
	)
	assert.NoError(t, err)

	memories, err := store.GetMemory(testCtx, testAppState, sessionID, 0)
	assert.NoError(t, err)
	assert.Equal(t, len(testMessages), len(memories.Messages))

	intentExtractor := NewIntentExtractor()

	for i, message := range memories.Messages {
		singleMessageEvent := &models.MessageEvent{
			SessionID: sessionID,
			Messages:  []models.Message{message},
		}

		err = intentExtractor.Extract(testCtx, testAppState, singleMessageEvent)
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

func TestIntentExtractor_Extract_OpenAI(t *testing.T) {
	appState.Config.LLM.Model = "gpt-3.5-turbo"
	llmClient, err := llms.NewOpenAILLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	runTestIntentExtractor(t, appState)
}

func TestIntentExtractor_Extract_Anthropic(t *testing.T) {
	appState.Config.LLM.Model = "claude-2"
	llmClient, err := llms.NewAnthropicLLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	runTestIntentExtractor(t, appState)
}
