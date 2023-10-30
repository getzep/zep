package llms

import (
	"context"

	"github.com/tmc/langchaingo/schema"

	"github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
	"github.com/pkoukk/tiktoken-go"
	"github.com/tmc/langchaingo/llms/openai"
)

const OpenAIAPIKeyNotSetError = "ZEP_OPENAI_API_KEY is not set" //nolint:gosec

var _ models.ZepLLM = &ZepOpenAILLM{}

func NewOpenAILLM(ctx context.Context, cfg *config.Config) (*ZepOpenAILLM, error) {
	zllm := &ZepOpenAILLM{}
	err := zllm.Init(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return zllm, nil
}

type ZepOpenAILLM struct {
	llm *openai.Chat
	tkm *tiktoken.Tiktoken
}

func (zllm *ZepOpenAILLM) Init(_ context.Context, cfg *config.Config) error {
	// Initialize the Tiktoken client
	encoding := "cl100k_base"
	tkm, err := tiktoken.GetEncoding(encoding)
	if err != nil {
		return err
	}
	zllm.tkm = tkm

	options, err := zllm.configureClient(cfg)
	if err != nil {
		return err
	}

	// Create a new client instance with options
	llm, err := NewOpenAIChatClient(options...)
	zllm.llm = llm

	return nil
}

func (zllm *ZepOpenAILLM) Call(ctx context.Context,
	prompt string,
	options ...llms.CallOption,
) (string, error) {
	// If the LLM is not initialized, return an error
	if zllm.llm == nil {
		return "", NewLLMError(InvalidLLMModelError, nil)
	}

	if len(options) == 0 {
		options = append(options, llms.WithTemperature(DefaultTemperature))
	}

	thisCtx, cancel := context.WithTimeout(ctx, OpenAIAPITimeout)
	defer cancel()

	messages := []schema.ChatMessage{schema.SystemChatMessage{Content: prompt}}

	completion, err := zllm.llm.Call(thisCtx, messages, options...)
	if err != nil {
		return "", err
	}

	return completion.GetContent(), nil
}

func (zllm *ZepOpenAILLM) EmbedTexts(ctx context.Context, texts []string) ([][]float32, error) {
	return EmbedTextsWithOpenAIClient(ctx, texts, zllm.llm, LLMClientType)
}

// GetTokenCount returns the number of tokens in the text
func (zllm *ZepOpenAILLM) GetTokenCount(text string) (int, error) {
	return len(zllm.tkm.Encode(text, nil, nil)), nil
}

func (zllm *ZepOpenAILLM) configureClient(cfg *config.Config) ([]openai.Option, error) {
	// Retrieve the OpenAIAPIKey from configuration
	apiKey := GetOpenAIAPIKey(cfg, LLMClientType)

	ValidateOpenAIConfig(cfg, LLMClientType)

	options := GetBaseOpenAIClientOptions(apiKey, cfg.LLM.Model)

	options = ConfigureOpenAIClientOptions(options, cfg, LLMClientType)

	return options, nil
}
