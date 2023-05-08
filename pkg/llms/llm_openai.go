package llms

import (
	"context"

	"github.com/danielchalef/zep/config"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/pkoukk/tiktoken-go"
	"github.com/sashabaranov/go-openai"
)

const OpenAIAPIKeyNotSetError = "ZEP_OPENAI_API_KEY is not set" //nolint:gosec
const InvalidLLMModelError = "llm model is not set or is invalid"

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
) (openai.ChatCompletionResponse, error) {
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
	resp, err := appState.OpenAIClient.CreateChatCompletion(ctx, req)
	if err != nil {
		return openai.ChatCompletionResponse{}, err
	}
	return resp, nil
}

func GetTokenCount(text string) (int, error) {
	encoding := "cl100k_base"
	tkm, err := tiktoken.GetEncoding(encoding)
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
