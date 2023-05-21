package llms

import (
	"context"
	"sync"
	"time"

	"github.com/avast/retry-go/v4"
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

func getTokenCountObject() (*tiktoken.Tiktoken, error) {
	once.Do(func() {
		encoding := "cl100k_base"
		tkm, tkmError = tiktoken.GetEncoding(encoding)
	})

	return tkm, tkmError
}

func CreateOpenAIClient(cfg *config.Config) *openai.Client {
	openAIKey := cfg.LLM.OpenAIAPIKey
	if openAIKey == "" {
		log.Fatal(OpenAIAPIKeyNotSetError)
	}
	return openai.NewClient(openAIKey)
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
	// Retry up to 3 times with exponential backoff, cancel after openAIAPITimeout
	retryCtx, cancel := context.WithTimeout(ctx, openAIAPITimeout)
	defer cancel()
	err = retry.Do(
		func() error {
			resp, err = appState.OpenAIClient.CreateChatCompletion(retryCtx, req)
			return err
		},
		retry.Attempts(3),
		retry.Context(retryCtx),
		retry.DelayType(retry.BackOffDelay),
		retry.OnRetry(func(n uint, err error) {
			log.Warningf("Retrying OpenAI API attempt #%d: %s\n", n, err)
		}),
	)
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
