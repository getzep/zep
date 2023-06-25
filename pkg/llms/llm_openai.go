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

const openAIAPITimeout = 60 * time.Second
const OpenAIAPIKeyNotSetError = "ZEP_OPENAI_API_KEY is not set" //nolint:gosec
const InvalidLLMModelError = "llm model is not set or is invalid"

var (
	once     sync.Once
	tkm      *tiktoken.Tiktoken
	tkmError error
)

func NewOpenAIRetryClient(cfg *config.Config) *openairetryclient.OpenAIRetryClient {
	apiKey := cfg.LLM.OpenAIAPIKey
	if apiKey == "" {
		log.Fatal(OpenAIAPIKeyNotSetError)
	}
	client := openai.NewClient(apiKey)
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
