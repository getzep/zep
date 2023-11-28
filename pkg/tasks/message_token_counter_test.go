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
) []models.Message {
	t.Helper()

	appState.LLMClient = llmClient

	store := appState.MemoryStore

	sessionID := testutils.GenerateRandomString(16)

	err := store.PutMemory(
		testCtx,
		sessionID,
		&models.Memory{Messages: testutils.TestMessages[:5]},
		true,
	)
	assert.NoError(t, err)

	messageList, err := store.GetMessageList(testCtx, sessionID, 0, 999)
	assert.NoError(t, err)

	messages := messageList.Messages

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

	memoryResult, err := store.GetMessageList(testCtx, sessionID, 0, 999)
	assert.NoError(t, err)
	assert.Equal(t, len(memoryResult.Messages), len(messages))

	// reverse order since select orders LIFO
	internal.ReverseSlice(memoryResult.Messages)

	return memoryResult.Messages
}

func TestTokenCountExtractor_OpenAI(t *testing.T) {
	appState.Config.LLM.Service = "openai"
	appState.Config.LLM.Model = "gpt-3.5-turbo"
	llmClient, err := llms.NewOpenAILLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	messages := runTestTokenCountExtractor(t, llmClient)

	for i := range messages {
		assert.True(t, messages[i].TokenCount > 0)
		assert.NotEmpty(t, messages[i].Content)
		assert.NotEmpty(t, messages[i].Role)
	}
}

func TestTokenCountExtractor_Anthropic(t *testing.T) {
	appState.Config.LLM.Service = "anthropic"
	appState.Config.LLM.Model = "claude-2"
	llmClient, err := llms.NewAnthropicLLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	messages := runTestTokenCountExtractor(t, llmClient)

	for i := range messages {
		assert.Zero(t, messages[i].TokenCount)
		assert.NotEmpty(t, messages[i].Content)
		assert.NotEmpty(t, messages[i].Role)
	}

	// reset config
	appState.Config = testutils.NewTestConfig()
}
