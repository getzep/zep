package llms

import (
	"context"
	"github.com/pkoukk/tiktoken-go"
	openai "github.com/sashabaranov/go-openai"
	"github.com/spf13/viper"
)

func CreateOpenAIClient() *openai.Client {
	openAIKey := viper.GetString("openai_api_key")
	if openAIKey == "" {
		log.Fatal("ZEP_OPENAI_API_KEY is not set")
	}
	return openai.NewClient(openAIKey)
}

func RunChatCompletion(
	ctx context.Context,
	openAIClient *openai.Client,
	summaryMaxTokens int,
	prompt string,
) (openai.ChatCompletionResponse, error) {
	modelName, err := GetLLMModelName()
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

	resp, err := openAIClient.CreateChatCompletion(ctx, req)
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

func GetLLMModelName() (string, error) {
	llmModel := viper.GetString("llm_model")
	if llmModel == "" || !ValidLLMMap[llmModel] {
		return "", NewLLMError("llm_model is not set or is invalid", nil)
	}
	return llmModel, nil
}
