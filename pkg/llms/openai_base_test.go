package llms

import (
	"testing"

	"github.com/tmc/langchaingo/llms/openai"

	"github.com/stretchr/testify/assert"
)

type TestCaseType string

const (
	OpenAIAPIKeyTestCase              TestCaseType = "OpenAIAPIKeyTestCase"
	AzureOpenAIEmbeddingModelTestCase TestCaseType = "AzureOpenAIEmbeddingModelTestCase"
	OpenAIEndpointTestCase            TestCaseType = "OpenAIEndpointTestCase"
	OpenAIOrgIDTestCase               TestCaseType = "OpenAIOrgIDTestCase"
)

var EmbeddingsTestTexts = []string{"Hello, world!", "Another text"}

func assertInit(t *testing.T, err error, openAIClient *openai.Chat, clientType ClientType) {
	t.Helper()
	assert.NoError(t, err, "Expected no error from Init")
	switch clientType {
	case EmbeddingsClientType:
		assert.NotNil(t, openAIClient, "Expected client to be initialized")
	default:
		assert.NotNil(t, openAIClient, "Expected llm to be initialized")
	}
}

func assertConfigureClient(t *testing.T, options []openai.Option, err error, testCase TestCaseType) {
	t.Helper()
	assert.NoError(t, err, "Unexpected error")
	expectedOptions := map[TestCaseType]int{
		OpenAIAPIKeyTestCase:              3,
		AzureOpenAIEmbeddingModelTestCase: 6,
		OpenAIEndpointTestCase:            4,
		OpenAIOrgIDTestCase:               4,
	}
	expected, ok := expectedOptions[testCase]
	if !ok {
		t.Errorf("Unexpected test case: %s", testCase)
		return
	}
	//? assert.Len(t, options, expected, "Unexpected number of options")
	if len(options) != expected {
		t.Errorf("Expected %d options, got %d", expected, len(options))
	}
}

func assertEmbeddings(t *testing.T, embeddings [][]float32, err error) {
	t.Helper()
	assert.NoError(t, err, "Expected no error from EmbedTexts")
	assert.Equal(t, len(EmbeddingsTestTexts), len(embeddings), "Expected embeddings to have same length as texts")
	assert.NotZero(t, embeddings[0], "Expected embeddings to be non-zero")
	assert.NotZero(t, embeddings[1], "Expected embeddings to be non-zero")
	assert.NoError(t, err, "Unexpected error")
}
