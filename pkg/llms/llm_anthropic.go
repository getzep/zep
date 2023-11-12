package llms

import (
	"context"
	"errors"
	"time"

	"github.com/tmc/langchaingo/llms/anthropic"

	"github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
)

const AnthropicAPITimeout = 30 * time.Second
const AnthropicAPIKeyNotSetError = "ZEP_ANTHROPIC_API_KEY is not set" //nolint:gosec

var _ models.ZepLLM = &ZepAnthropicLLM{}

func NewAnthropicLLM(ctx context.Context, cfg *config.Config) (models.ZepLLM, error) {
	zllm := &ZepLLM{
		llm: &ZepAnthropicLLM{
			cfg: cfg,
		},
	}
	err := zllm.Init(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return zllm, nil
}

type ZepAnthropicLLM struct {
	client *anthropic.LLM
	cfg    *config.Config
}

func (zllm *ZepAnthropicLLM) Init(_ context.Context, cfg *config.Config) error {
	options, err := zllm.configureClient(cfg)
	if err != nil {
		return err
	}

	// Create a new client instance with options
	llm, err := anthropic.New(options...)
	if err != nil {
		return err
	}
	zllm.client = llm

	return nil
}

func (zllm *ZepAnthropicLLM) Call(ctx context.Context,
	prompt string,
	options ...llms.CallOption,
) (string, error) {
	// If the LLM is not initialized, return an error
	if zllm.client == nil {
		return "", NewLLMError(InvalidLLMModelError, nil)
	}

	if len(options) == 0 {
		options = append(options, llms.WithTemperature(DefaultTemperature))
	}

	thisCtx, cancel := context.WithTimeout(ctx, AnthropicAPITimeout)
	defer cancel()

	prompt = "Human: " + prompt + "\nAssistant:"

	completion, err := zllm.client.Call(thisCtx, prompt, options...)
	if err != nil {
		return "", err
	}

	return completion, nil
}

func (zllm *ZepAnthropicLLM) EmbedTexts(_ context.Context, _ []string) ([][]float32, error) {
	return nil, errors.New("not implemented. use a local embedding model")
}

// GetTokenCount returns the number of tokens in the text.
// Return 0 for now, since we don't have a token count function
func (zllm *ZepAnthropicLLM) GetTokenCount(_ string) (int, error) {
	return 0, nil
}

func (zllm *ZepAnthropicLLM) configureClient(cfg *config.Config) ([]anthropic.Option, error) {
	apiKey := cfg.LLM.AnthropicAPIKey
	// If the key is not set, log a fatal error and exit
	if apiKey == "" {
		log.Fatal(AnthropicAPIKeyNotSetError)
	}

	options := make([]anthropic.Option, 0)
	options = append(
		options,
		anthropic.WithModel(cfg.LLM.Model),
		anthropic.WithToken(apiKey),
	)

	return options, nil
}
