package llms

import (
	"context"
	"time"

	"github.com/tmc/langchaingo/schema"

	"github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
	"github.com/pkoukk/tiktoken-go"
	"github.com/tmc/langchaingo/llms/openai"
)

const OpenAICallTimeout = 60 * time.Second
const OpenAIAPITimeout = 20 * time.Second
const OpenAIAPIKeyNotSetError = "ZEP_OPENAI_API_KEY is not set" //nolint:gosec
const MaxOpenAIAPIRequestAttempts = 5

var _ models.ZepLLM = &ZepOpenAILLM{}

func NewOpenAILLM(ctx context.Context, cfg *config.Config) (models.ZepLLM, error) {
	zllm := &ZepLLM{
		llm: &ZepOpenAILLM{
			cfg: cfg,
		},
	}
	err := zllm.Init(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return zllm, nil
}

type ZepOpenAILLM struct {
	client *openai.Chat
	cfg    *config.Config
	tkm    *tiktoken.Tiktoken
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
	llm, err := openai.NewChat(options...)
	if err != nil {
		return err
	}
	zllm.client = llm

	return nil
}

func (zllm *ZepOpenAILLM) Call(ctx context.Context,
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

	ctx, cancel := context.WithTimeout(ctx, OpenAICallTimeout)
	defer cancel()

	messages := []schema.ChatMessage{schema.SystemChatMessage{Content: prompt}}

	completion, err := zllm.client.Call(ctx, messages, options...)
	if err != nil {
		return "", err
	}

	return completion.GetContent(), nil
}

func (zllm *ZepOpenAILLM) EmbedTexts(ctx context.Context, texts []string) ([][]float32, error) {
	// If the LLM is not initialized, return an error
	if zllm.client == nil {
		return nil, NewLLMError(InvalidLLMModelError, nil)
	}

	ctx, cancel := context.WithTimeout(ctx, OpenAICallTimeout)
	defer cancel()

	embeddings, err := zllm.client.CreateEmbedding(ctx, texts)
	if err != nil {
		return nil, NewLLMError("error while creating embedding", err)
	}

	return embeddings, nil
}

// GetTokenCount returns the number of tokens in the text
func (zllm *ZepOpenAILLM) GetTokenCount(text string) (int, error) {
	return len(zllm.tkm.Encode(text, nil, nil)), nil
}

func (zllm *ZepOpenAILLM) configureClient(cfg *config.Config) ([]openai.Option, error) {
	// Retrieve the OpenAIAPIKey from configuration
	apiKey := cfg.LLM.OpenAIAPIKey
	// If the key is not set, log a fatal error and exit
	if apiKey == "" {
		log.Fatal(OpenAIAPIKeyNotSetError)
	}
	if cfg.LLM.AzureOpenAIEndpoint != "" && cfg.LLM.OpenAIEndpoint != "" {
		log.Fatal("only one of AzureOpenAIEndpoint or OpenAIEndpoint can be set")
	}

	// Set up the HTTP client and config OpenTelemetry wrapper
	httpClient := NewRetryableHTTPClient(MaxOpenAIAPIRequestAttempts, OpenAIAPITimeout)

	options := make([]openai.Option, 0)
	options = append(
		options,
		openai.WithHTTPClient(httpClient),
		openai.WithModel(cfg.LLM.Model),
		openai.WithToken(apiKey),
	)

	switch {
	case cfg.LLM.AzureOpenAIEndpoint != "":
		// Check configuration for AzureOpenAIEndpoint; if it's set, use the DefaultAzureConfig
		// and provided endpoint Path
		options = append(
			options,
			openai.WithAPIType(openai.APITypeAzure),
			openai.WithBaseURL(cfg.LLM.AzureOpenAIEndpoint),
		)
		if cfg.LLM.AzureOpenAIModel.EmbeddingDeployment != "" {
			options = append(
				options,
				openai.WithEmbeddingModel(cfg.LLM.AzureOpenAIModel.EmbeddingDeployment),
			)
		}
	case cfg.LLM.OpenAIEndpoint != "":
		// If an alternate OpenAI-compatible endpoint Path is set, use this as the base Path for requests
		options = append(
			options,
			openai.WithBaseURL(cfg.LLM.OpenAIEndpoint),
		)
	case cfg.LLM.OpenAIOrgID != "":
		options = append(options, openai.WithOrganization(cfg.LLM.OpenAIOrgID))
	}

	return options, nil
}
