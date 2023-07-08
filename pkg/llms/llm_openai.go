package llms

import (
	"context"
	"sync"
	"time"

	"github.com/getzep/zep/pkg/llms/openairetryclient"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
	"github.com/pkoukk/tiktoken-go"
	"github.com/sashabaranov/go-openai"
)

const openAIAPITimeout = 90 * time.Second
const OpenAIAPIKeyNotSetError = "ZEP_OPENAI_API_KEY is not set" //nolint:gosec
const InvalidLLMModelError = "llm model is not set or is invalid"

var (
	once     sync.Once
	tkm      *tiktoken.Tiktoken
	tkmError error
)

func NewOpenAIRetryClient(cfg *config.Config) *openairetryclient.OpenAIRetryClient {
	// Retrieve the OpenAIAPIKey from configuration
	apiKey := cfg.LLM.OpenAIAPIKey
	// If the key is not set, log a fatal error and exit
	if apiKey == "" {
		log.Fatal(OpenAIAPIKeyNotSetError)
	}
	if cfg.LLM.AzureOpenAIEndpoint != "" && cfg.LLM.OpenAIEndpoint != "" {
		log.Fatal("only one of AzureOpenAIEndpoint or OpenAIEndpoint can be set")
	}

	// Initiate the openAIClientConfig with the default configuration
	openAIClientConfig := openai.DefaultConfig(apiKey)

	switch {
	case cfg.LLM.AzureOpenAIEndpoint != "":
		// Check configuration for AzureOpenAIEndpoint; if it's set, use the DefaultAzureConfig
		// and provided endpoint URL
		openAIClientConfig = openai.DefaultAzureConfig(apiKey, cfg.LLM.AzureOpenAIEndpoint)
	case cfg.LLM.OpenAIEndpoint != "":
		// If an alternate OpenAI-compatible endpoint URL is set, use this as the base URL for requests
		openAIClientConfig.BaseURL = cfg.LLM.OpenAIEndpoint
	default:
		// If no specific endpoints are defined, use the default configuration with the OpenAIOrgID
		// This optional and may just be an empty string
		openAIClientConfig.OrgID = cfg.LLM.OpenAIOrgID
	}

	// Create a new client instance with the final openAIClientConfig
	client := openai.NewClientWithConfig(openAIClientConfig)

	// Return a new retry client. This client contains a pre-configured OpenAI client
	// and additional retry logic (timeout duration and maximum number of attempts)
	return &openairetryclient.OpenAIRetryClient{
		Client: *client,
		Config: struct {
			Timeout     time.Duration
			MaxAttempts uint
		}{
			Timeout:     openAIAPITimeout,
			MaxAttempts: 5,
		},
	}
}

func getTokenCountObject() (*tiktoken.Tiktoken, error) {
	once.Do(func() {
		encoding := "cl100k_base"
		tkm, tkmError = tiktoken.GetEncoding(encoding)
	})

	return tkm, tkmError
}

func RunChatCompletion(
	ctx context.Context,
	appState *models.AppState,
	summaryMaxTokens int,
	prompt string,
) (resp openai.ChatCompletionResponse, err error) {
	modelName, err := GetLLMModelName(appState.Config)
	if err != nil {
		return openai.ChatCompletionResponse{}, err
	}
	req := openai.ChatCompletionRequest{
		Model:     modelName,
		MaxTokens: summaryMaxTokens,
		Messages: []openai.ChatCompletionMessage{
			{
				Role:    openai.ChatMessageRoleUser,
				Content: prompt,
			},
		},
		Temperature: DefaultTemperature,
	}
	resp, err = appState.OpenAIClient.CreateChatCompletion(ctx, req)
	if err != nil {
		return openai.ChatCompletionResponse{}, err
	}
	return resp, nil
}

func GetTokenCount(text string) (int, error) {
	tkm, err := getTokenCountObject()
	if err != nil {
		return 0, err
	}
	if err != nil {
		return 0, err
	}
	return len(tkm.Encode(text, nil, nil)), nil
}

func GetLLMModelName(cfg *config.Config) (string, error) {
	llmModel := cfg.LLM.Model
	if llmModel == "" || !ValidLLMMap[llmModel] {
		return "", NewLLMError(InvalidLLMModelError, nil)
	}
	return llmModel, nil
}
