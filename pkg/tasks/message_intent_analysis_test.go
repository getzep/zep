package tasks

import (
	"testing"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func runTestIntentExtractor(t *testing.T, testAppState *models.AppState) {
	store := testAppState.MemoryStore

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	testMessages := testutils.TestMessages[:2]

	err = store.PutMemory(
		testCtx,
		sessionID,
		&models.Memory{Messages: testMessages},
		true,
	)
	assert.NoError(t, err)

	memories, err := store.GetMemory(testCtx, sessionID, 0)
	assert.NoError(t, err)
	assert.Equal(t, len(testMessages), len(memories.Messages))

	intentTask := NewMessageIntentTask(testAppState)
	errs := make(chan error, len(memories.Messages))

	for _, message := range memories.Messages {
		intentTask.processMessage(testCtx, appState, message, sessionID, errs)

	}

	close(errs)
	for err := range errs {
		assert.NoError(t, err)
	}

	memories, err = store.GetMemory(testCtx, sessionID, 0)
	assert.NoError(t, err)
	for _, message := range memories.Messages {
		metadata := message.Metadata["system"]
		if metadata != nil {
			if metadataMap, ok := metadata.(map[string]interface{}); ok {
				assert.NotNil(t, metadataMap["intent"])
			}
		}
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

	//
	appState.Config = testutils.NewTestConfig()
}
