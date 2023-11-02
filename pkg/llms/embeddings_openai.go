package llms

import (
	"context"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
	"github.com/tmc/langchaingo/llms/openai"
)

const EmbeddingsOpenAIAPIKeyNotSetError = "ZEP_EMBEDDINGS_OPENAI_API_KEY is not set" //nolint:gosec

var _ models.ZepEmbeddingsClient = &ZepOpenAIEmbeddingsClient{}

func NewOpenAIEmbeddingsClient(ctx context.Context, cfg *config.Config) (*ZepOpenAIEmbeddingsClient, error) {
	zembeddings := &ZepOpenAIEmbeddingsClient{}
	err := zembeddings.Init(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return zembeddings, nil
}

type ZepOpenAIEmbeddingsClient struct {
	client *openai.Chat
}

func (zembeddings *ZepOpenAIEmbeddingsClient) Init(_ context.Context, cfg *config.Config) error {
	options, err := zembeddings.configureClient(cfg)
	if err != nil {
		return err
	}

	// Create a new client instance with options.
	// Even if it will just used for embeddings,
	// it uses the same langchain openai chat client builder
	client, err := openai.NewChat(options...)
	if err != nil {
		return err
	}

	zembeddings.client = client

	return nil
}

func (zembeddings *ZepOpenAIEmbeddingsClient) EmbedTexts(ctx context.Context, texts []string) ([][]float32, error) {
	return EmbedTextsWithOpenAIClient(ctx, texts, zembeddings.client, EmbeddingsClientType)
}

func getValidOpenAIModel() string {
	for k := range ValidOpenAILLMs {
		return k
	}
	return "gpt-3.5-turbo"
}

func (zembeddings *ZepOpenAIEmbeddingsClient) configureClient(cfg *config.Config) ([]openai.Option, error) {
	// Retrieve the OpenAIAPIKey from configuration
	apiKey := GetOpenAIAPIKey(cfg, EmbeddingsClientType)

	ValidateOpenAIConfig(cfg, EmbeddingsClientType)

	// Even if it will only be used for embeddings, we should pass a valid openai llm model
	// to avoid any errors
	validOpenaiLLMModel := getValidOpenAIModel()

	options := GetBaseOpenAIClientOptions(apiKey, validOpenaiLLMModel)

	options = ConfigureOpenAIClientOptions(options, cfg, EmbeddingsClientType)

	return options, nil
}
