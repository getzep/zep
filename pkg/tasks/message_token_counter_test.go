package tasks

import (
	"encoding/json"
	"testing"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/llms"

	"github.com/getzep/zep/internal"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func runTestTokenCountExtractor(
	t *testing.T,
	llmClient models.ZepLLM,
) *models.Memory {
	appState.LLMClient = llmClient

	store := appState.MemoryStore

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	err = store.PutMemory(
		testCtx,
		appState,
		sessionID,
		&models.Memory{Messages: testutils.TestMessages[:5]},
		true,
	)
	assert.NoError(t, err)

	memoryConfig := &models.MemoryConfig{
		SessionID:     sessionID,
		LastNMessages: 0,
		Type:          models.SimpleMemoryType,
	}

	memories, err := store.GetMemory(testCtx, appState, memoryConfig)
	assert.NoError(t, err)

	messages := memories.Messages

	tokenCountExtractor := NewMessageTokenCountTask(appState)

	p, err := json.Marshal(messages)
	assert.NoError(t, err)

	m := &message.Message{
		Metadata: message.Metadata{
			"session_id": sessionID,
		},
		Payload: p,
	}

	err = tokenCountExtractor.Execute(testCtx, m)
	assert.NoError(t, err)

	memory, err := appState.MemoryStore.GetMemory(testCtx, appState, memoryConfig)
	assert.NoError(t, err)
	assert.Equal(t, len(memory.Messages), len(messages))

	// reverse order since select orders LIFO
	internal.ReverseSlice(memory.Messages)

	return memory
}

func TestTokenCountExtractor_OpenAI(t *testing.T) {
	appState.Config.LLM.Service = "openai"
	appState.Config.LLM.Model = "gpt-3.5-turbo"
	llmClient, err := llms.NewOpenAILLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	memory := runTestTokenCountExtractor(t, llmClient)

	for i := range memory.Messages {
		assert.True(t, memory.Messages[i].TokenCount > 0)
		assert.NotEmpty(t, memory.Messages[i].Content)
		assert.NotEmpty(t, memory.Messages[i].Role)
	}
}

func TestTokenCountExtractor_Anthropic(t *testing.T) {
	appState.Config.LLM.Service = "anthropic"
	appState.Config.LLM.Model = "claude-2"
	llmClient, err := llms.NewAnthropicLLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	memory := runTestTokenCountExtractor(t, llmClient)

	for i := range memory.Messages {
		assert.Zero(t, memory.Messages[i].TokenCount)
		assert.NotEmpty(t, memory.Messages[i].Content)
		assert.NotEmpty(t, memory.Messages[i].Role)
	}

	// reset config
	appState.Config = testutils.NewTestConfig()
}
