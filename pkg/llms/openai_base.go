package llms

import (
	"context"
	"time"

	"github.com/getzep/zep/config"
	"github.com/tmc/langchaingo/llms/openai"
)

const OpenAIAPITimeout = 90 * time.Second
const MaxOpenAIAPIRequestAttempts = 5

type ClientType string

const (
	EmbeddingsClientType ClientType = "embeddings"
	LLMClientType        ClientType = "llm"
)

func NewOpenAIChatClient(options ...openai.Option) (*openai.Chat, error) {
	client, err := openai.NewChat(options...)
	if err != nil {
		return nil, err
	}
	return client, nil
}

func GetOpenAIAPIKey(cfg *config.Config, clientType ClientType) string {
	var apiKey string

	if clientType == EmbeddingsClientType {
		apiKey = cfg.EmbeddingsClient.OpenAIAPIKey
		// If the key is not set, log a fatal error and exit
		if apiKey == "" {
			log.Fatal(EmbeddingsOpenAIAPIKeyNotSetError)
		}
	} else {
		apiKey = cfg.LLM.OpenAIAPIKey
		if apiKey == "" {
			log.Fatal(OpenAIAPIKeyNotSetError)
		}
	}
	return apiKey
}

func validateOpenAIConfig(cfg *config.Config, clientType ClientType) {

	var azureEndpoint string
	var openAIEndpoint string

	if clientType == EmbeddingsClientType {
		azureEndpoint = cfg.EmbeddingsClient.AzureOpenAIEndpoint
		openAIEndpoint = cfg.EmbeddingsClient.OpenAIEndpoint
	} else {
		azureEndpoint = cfg.LLM.AzureOpenAIEndpoint
		openAIEndpoint = cfg.LLM.OpenAIEndpoint
	}

	if azureEndpoint != "" && openAIEndpoint != "" {
		log.Fatal("only one of AzureOpenAIEndpoint or OpenAIEndpoint can be set")
	}
}

func EmbedTextsWithOpenAIClient(ctx context.Context, texts []string, openAIClient *openai.Chat, clientType ClientType) ([][]float32, error) {
	// If the Client is not initialized, return an error
	if openAIClient == nil {
		if clientType == EmbeddingsClientType {
			return nil, NewEmbeddingsClientError(InvalidEmbeddingsClientError, nil)
		}
		return nil, NewLLMError(InvalidLLMModelError, nil)
	}

	thisCtx, cancel := context.WithTimeout(ctx, OpenAIAPITimeout)
	defer cancel()

	embeddings, err := openAIClient.CreateEmbedding(thisCtx, texts)
	if err != nil {
		message := "error while creating embedding"
		if clientType == EmbeddingsClientType {
			return nil, NewEmbeddingsClientError(message, nil)
		}
		return nil, NewLLMError(message, err)
	}

	return embeddings, nil
}

func GetBaseOpenAIClientOptions(apiKey, validModel string) []openai.Option {
	retryableHTTPClient := NewRetryableHTTPClient(MaxOpenAIAPIRequestAttempts, OpenAIAPITimeout)

	options := make([]openai.Option, 0)
	options = append(
		options,
		openai.WithHTTPClient(retryableHTTPClient.StandardClient()),
		openai.WithModel(validModel),
		openai.WithToken(apiKey),
	)

	return options
}

func ConfigureOpenAIClientOptions(options []openai.Option, cfg *config.Config, clientType ClientType) []openai.Option {
	applyOption := func(cond bool, opts ...openai.Option) []openai.Option {
		if cond {
			return append(options, opts...)
		}
		return options
	}

	var openAIEndpoint string
	var openAIOrgID string

	if clientType == EmbeddingsClientType {
		openAIEndpoint = cfg.EmbeddingsClient.OpenAIEndpoint
		openAIOrgID = cfg.EmbeddingsClient.OpenAIOrgID

		// Check configuration for AzureOpenAIEndpoint; if it's set, use the DefaultAzureConfig
		// and provided endpoint Path.
		// WithEmbeddings is always required in case of embeddings client
		options = applyOption(cfg.EmbeddingsClient.AzureOpenAIEndpoint != "",
			openai.WithAPIType(openai.APITypeAzure),
			openai.WithBaseURL(cfg.EmbeddingsClient.AzureOpenAIEndpoint),
			openai.WithEmbeddingModel(cfg.EmbeddingsClient.AzureOpenAIModel.EmbeddingDeployment),
		)
	} else {
		openAIEndpoint = cfg.LLM.OpenAIEndpoint
		openAIOrgID = cfg.LLM.OpenAIOrgID

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

	}

	options = applyOption(openAIEndpoint != "",
		openai.WithBaseURL(openAIEndpoint),
	)

	options = applyOption(openAIOrgID != "",
		openai.WithOrganization(openAIOrgID),
	)

	return options
}
