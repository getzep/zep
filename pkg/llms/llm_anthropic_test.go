package llms

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/stretchr/testify/assert"

	"github.com/getzep/zep/config"
)

func TestZepAnthropicLLM_Init(t *testing.T) {
	cfg := &config.Config{
		LLM: config.LLM{
			Model:           "claude-2",
			AnthropicAPIKey: "test-key",
		},
	}

	zllm, err := NewAnthropicLLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewAnthropicLLM")

	z, ok := zllm.(*ZepLLM)
	assert.True(t, ok, "Expected ZepLLM")
	assert.NotNil(t, z.llm, "Expected llm to be initialized")

	a, ok := z.llm.(*ZepAnthropicLLM)
	assert.True(t, ok, "Expected ZepOpenAILLM")
	assert.NotNil(t, a.client, "Expected client to be initialized")
}

func TestZepAnthropicLLM_Call(t *testing.T) {
	cfg := testutils.NewTestConfig()
	cfg.LLM.Model = "claude-2"

	zllm, err := NewAnthropicLLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	prompt := "Hello, world!"
	result, err := zllm.Call(context.Background(), prompt)
	assert.NoError(t, err, "Expected no error from Call")

	assert.NotEmpty(t, result, "Expected result to be non-empty")
}

func TestZepAnthropicLLM_EmbedTexts(t *testing.T) {
	cfg := testutils.NewTestConfig()

	zllm, err := NewAnthropicLLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	texts := []string{"Hello, world!", "Another text"}
	_, err = zllm.EmbedTexts(context.Background(), texts)
	assert.ErrorContains(t, err, "not implemented", "Expected error from EmbedTexts")
}

func TestZepAnthropicLLM_GetTokenCount(t *testing.T) {
	cfg := testutils.NewTestConfig()

	zllm, err := NewAnthropicLLM(context.Background(), cfg)
	assert.NoError(t, err, "Expected no error from NewOpenAILLM")

	count, err := zllm.GetTokenCount("Hello, world!")
	assert.NoError(t, err, "Expected no error from GetTokenCount")

	// Should return 0
	assert.Equal(t, 0, count, "Unexpected token count")
}
