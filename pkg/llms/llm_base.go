package llms

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/tmc/langchaingo/llms/openai"

	"github.com/hashicorp/go-retryablehttp"

	"github.com/getzep/zep/config"

	"github.com/getzep/zep/internal"
)

const DefaultTemperature = 0.0
const InvalidLLMModelError = "llm model is not set or is invalid"
const InvalidEmbeddingsClientError = "embeddings client is not set or is invalid"

var InvalidEmbeddingsDeploymentError = func(service string) error {
	return fmt.Errorf("invalid embeddings deployment for %s, deployment name is required", service)
}

var log = internal.GetLogger()

func NewLLMClient(ctx context.Context, cfg *config.Config) (models.ZepLLM, error) {
	switch cfg.LLM.Service {
	case "openai":
		// Azure OpenAI model names can't be validated by any hard-coded models
		// list as it is configured by custom deployment name that may or may not match the model name.
		// We will copy the Model name value down to AzureOpenAI LLM Deployment
		// to assume user deployed base model with matching deployment name as
		// advised by Microsoft, but still support custom models or otherwise-named
		// base model.
		if cfg.LLM.AzureOpenAIEndpoint != "" {
			if cfg.LLM.AzureOpenAIModel.LLMDeployment != "" {
				cfg.LLM.Model = cfg.LLM.AzureOpenAIModel.LLMDeployment
			}
			if cfg.LLM.Model == "" {
				return nil, fmt.Errorf(
					"invalid llm deployment for %s, deployment name is required",
					cfg.LLM.Service,
				)
			}

			// EmbeddingsDeployment is only required if Zep is also configured to use
			// OpenAI embeddings for document or message extractors
			if cfg.LLM.AzureOpenAIModel.EmbeddingDeployment == "" && useOpenAIEmbeddings(cfg) {
				err := InvalidEmbeddingsDeploymentError(cfg.EmbeddingsClient.Service)
				return nil, err
			}
			return NewOpenAILLM(ctx, cfg)
		}
		// if custom OpenAI Endpoint is set, do not validate model name
		if cfg.LLM.OpenAIEndpoint != "" {
			return NewOpenAILLM(ctx, cfg)
		}
		// Otherwise, validate model name
		if _, ok := ValidOpenAILLMs[cfg.LLM.Model]; !ok {
			return nil, fmt.Errorf(
				"invalid llm model \"%s\" for %s",
				cfg.LLM.Model,
				cfg.LLM.Service,
			)
		}
		return NewOpenAILLM(ctx, cfg)
	case "anthropic":
		if _, ok := ValidAnthropicLLMs[cfg.LLM.Model]; !ok {
			return nil, fmt.Errorf(
				"invalid llm model \"%s\" for %s",
				cfg.LLM.Model,
				cfg.LLM.Service,
			)
		}
		return NewAnthropicLLM(ctx, cfg)
	case "":
		// for backward compatibility
		return NewOpenAILLM(ctx, cfg)
	default:
		return nil, fmt.Errorf("invalid LLM service: %s", cfg.LLM.Service)
	}
}

func NewEmbeddingsClient(ctx context.Context, cfg *config.Config) (models.ZepEmbeddingsClient, error) {
	switch cfg.EmbeddingsClient.Service {
	// For now we only support OpenAI embeddings
	case "openai":
		// EmbeddingsDeployment is required if using external embeddings with AzureOpenAI
		if cfg.EmbeddingsClient.AzureOpenAIEndpoint != "" && cfg.EmbeddingsClient.AzureOpenAIModel.EmbeddingDeployment == "" {
			err := InvalidEmbeddingsDeploymentError(cfg.EmbeddingsClient.Service)
			return nil, err
		}
		// The logic is the same if custom OpenAI Endpoint is set or not
		// since the model name will be set automatically in this case
		return NewOpenAIEmbeddingsClient(ctx, cfg)
	case "":
		return NewOpenAIEmbeddingsClient(ctx, cfg)
	default:
		return nil, fmt.Errorf("invalid embeddings service: %s", cfg.EmbeddingsClient.Service)
	}
}

type LLMError struct {
	message       string
	originalError error
}

func (e *LLMError) Error() string {
	return fmt.Sprintf("llm error: %s (original error: %v)", e.message, e.originalError)
}

func NewLLMError(message string, originalError error) *LLMError {
	return &LLMError{message: message, originalError: originalError}
}

var ValidOpenAILLMs = map[string]bool{
	"gpt-3.5-turbo":     true,
	"gpt-4":             true,
	"gpt-3.5-turbo-16k": true,
	"gpt-4-32k":         true,
}

var ValidAnthropicLLMs = map[string]bool{
	"claude-instant-1": true,
	"claude-2":         true,
}

var ValidLLMMap = internal.MergeMaps(ValidOpenAILLMs, ValidAnthropicLLMs)

var MaxLLMTokensMap = map[string]int{
	"gpt-3.5-turbo":     4096,
	"gpt-3.5-turbo-16k": 16_384,
	"gpt-4":             8192,
	"gpt-4-32k":         32_768,
	"claude-instant-1":  100_000,
	"claude-2":          100_000,
}

func GetLLMModelName(cfg *config.Config) (string, error) {
	llmModel := cfg.LLM.Model
	// Don't validate if custom OpenAI endpoint or Azure OpenAI endpoint is set
	if cfg.LLM.OpenAIEndpoint != "" || cfg.LLM.AzureOpenAIEndpoint != "" {
		return llmModel, nil
	}
	if llmModel == "" || !ValidLLMMap[llmModel] {
		return "", NewLLMError(InvalidLLMModelError, nil)
	}
	return llmModel, nil
}

func Float64ToFloat32Matrix(in [][]float64) [][]float32 {
	out := make([][]float32, len(in))
	for i := range in {
		out[i] = make([]float32, len(in[i]))
		for j, v := range in[i] {
			out[i][j] = float32(v)
		}
	}

	return out
}

func NewRetryableHTTPClient(retryMax int, timeout time.Duration) *retryablehttp.Client {
	retryableHTTPClient := retryablehttp.NewClient()
	retryableHTTPClient.RetryMax = retryMax
	retryableHTTPClient.HTTPClient.Timeout = timeout
	retryableHTTPClient.Logger = log
	retryableHTTPClient.Backoff = retryablehttp.DefaultBackoff
	retryableHTTPClient.CheckRetry = retryPolicy

	return retryableHTTPClient
}

// retryPolicy is a retryablehttp.CheckRetry function. It is used to determine
// whether a request should be retried or not.
func retryPolicy(ctx context.Context, resp *http.Response, err error) (bool, error) {
	// do not retry on context.Canceled or context.DeadlineExceeded
	if ctx.Err() != nil {
		return false, ctx.Err()
	}

	// Do not retry 400 errors as they're used by OpenAI to indicate maximum
	// context length exceeded
	if resp != nil && resp.StatusCode == 400 {
		return false, err
	}

	shouldRetry, _ := retryablehttp.DefaultRetryPolicy(ctx, resp, err)
	return shouldRetry, nil
}

// useOpenAIEmbeddings is true if OpenAI embeddings are enabled
func useOpenAIEmbeddings(cfg *config.Config) bool {
	switch {
	case cfg.Extractors.Messages.Embeddings.Enabled:
		return cfg.Extractors.Messages.Embeddings.Service == "openai"
	case cfg.Extractors.Documents.Embeddings.Enabled:
		return cfg.Extractors.Documents.Embeddings.Service == "openai"
	}

	return false
}

func NewOpenAIChatClient(options ...openai.Option) (*openai.Chat, error) {
	client, err := openai.NewChat(options...)
	if err != nil {
		return nil, err
	}
	return client, nil
}

func GetOpenAIAPIKey(cfg *config.Config, clientType string) string {
	var apiKey string

	if clientType == "embeddings" {
		apiKey = cfg.EmbeddingsClient.OpenAIAPIKey
		// If the key is not set, log a fatal error and exit
		if apiKey == "" {
			log.Fatal(EmbeddingsOpenAIAPIKeyNotSetError)
		}
	} else {
		apiKey = cfg.LLM.OpenAIAPIKey
		if apiKey == "" {
			log.Fatal(EmbeddingsOpenAIAPIKeyNotSetError)
		}
	}
	return apiKey
}

func EmbedTextsWithOpenAIClient(ctx context.Context, texts []string, openAIClient *openai.Chat) ([][]float32, error) {
	// If the LLM is not initialized, return an error
	if openAIClient == nil {
		return nil, NewLLMError(InvalidLLMModelError, nil)
	}

	thisCtx, cancel := context.WithTimeout(ctx, OpenAIAPITimeout)
	defer cancel()

	embeddings, err := openAIClient.CreateEmbedding(thisCtx, texts)
	if err != nil {
		return nil, NewLLMError("error while creating embedding", err)
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
