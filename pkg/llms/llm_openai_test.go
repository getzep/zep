package llms

import (
	"testing"
	"time"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/llms/openairetryclient"
	"github.com/stretchr/testify/assert"
)

// Minimal set of test cases. We'd need to refactor the error states to not immediately
// exit the program to test more thoroughly.

// Test with a valid Azure configuration.
func TestNewOpenAIRetryClient_ValidAzureConfig(t *testing.T) {
	cfg := &config.Config{
		LLM: config.LLM{
			OpenAIAPIKey:        "testKey",
			AzureOpenAIEndpoint: "azureEndpoint",
		},
	}

	client := NewOpenAIRetryClient(cfg)
	assert.IsType(t, &openairetryclient.OpenAIRetryClient{}, client)
	assert.IsType(t, time.Duration(0), client.Config.Timeout)
	assert.Equal(t, uint(5), client.Config.MaxAttempts)
}

// Test with a valid configuration.
func TestNewOpenAIRetryClient_ValidConfig(t *testing.T) {
	cfg := &config.Config{
		LLM: config.LLM{
			OpenAIAPIKey: "testKey",
		},
	}

	client := NewOpenAIRetryClient(cfg)
	assert.IsType(t, &openairetryclient.OpenAIRetryClient{}, client)
	assert.IsType(t, time.Duration(0), client.Config.Timeout)
	assert.Equal(t, uint(5), client.Config.MaxAttempts)
}

// Test with a valid configuration.
func TestNewOpenAIRetryClient_ValidConfigCustomEndpoint(t *testing.T) {
	cfg := &config.Config{
		LLM: config.LLM{
			OpenAIAPIKey:   "testKey",
			OpenAIEndpoint: "https://api.openai.com/v1",
		},
	}

	client := NewOpenAIRetryClient(cfg)
	assert.IsType(t, &openairetryclient.OpenAIRetryClient{}, client)
	assert.IsType(t, time.Duration(0), client.Config.Timeout)
	assert.Equal(t, uint(5), client.Config.MaxAttempts)
}
