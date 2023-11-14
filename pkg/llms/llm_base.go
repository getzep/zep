package llms

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptrace"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/httptrace/otelhttptrace"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"

	"github.com/getzep/zep/pkg/models"
	"github.com/tmc/langchaingo/llms"
	"go.opentelemetry.io/otel/trace"

	"github.com/hashicorp/go-retryablehttp"

	"github.com/getzep/zep/config"

	"github.com/getzep/zep/internal"
)

const DefaultTemperature = 0.0
const InvalidLLMModelError = "llm model is not set or is invalid"
const OtelLLMTracerName = "llm"

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
				return nil, fmt.Errorf(
					"invalid embeddings deployment for %s, deployment name is required",
					cfg.LLM.Service,
				)
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

var _ models.ZepLLM = &ZepLLM{}

// ZepLLM is a wrapper around the Zep LLM implementations that implements the
// ZepLLM interface and adds OpenTelemetry tracing
type ZepLLM struct {
	llm    models.ZepLLM
	tracer trace.Tracer
}

func (zllm *ZepLLM) Call(ctx context.Context,
	prompt string,
	options ...llms.CallOption,
) (string, error) {
	ctx, span := zllm.tracer.Start(ctx, "llm.Call")
	defer span.End()

	result, err := zllm.llm.Call(ctx, prompt, options...)
	if err != nil {
		span.RecordError(err)
		return "", err
	}

	return result, err
}

func (zllm *ZepLLM) EmbedTexts(ctx context.Context, texts []string) ([][]float32, error) {
	ctx, span := zllm.tracer.Start(ctx, "llm.EmbedTexts")
	defer span.End()

	result, err := zllm.llm.EmbedTexts(ctx, texts)
	if err != nil {
		span.RecordError(err)
		return nil, err
	}

	return result, err
}

func (zllm *ZepLLM) GetTokenCount(text string) (int, error) {
	return zllm.llm.GetTokenCount(text)
}

func (zllm *ZepLLM) Init(ctx context.Context, cfg *config.Config) error {
	// set up tracing
	tracer := otel.Tracer(OtelLLMTracerName)
	zllm.tracer = tracer

	return zllm.llm.Init(ctx, cfg)
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
	"gpt-3.5-turbo":      true,
	"gpt-4":              true,
	"gpt-3.5-turbo-16k":  true,
	"gpt-3.5-turbo-1106": true,
	"gpt-4-32k":          true,
	"gpt-4-1106-preview": true,
}

var ValidAnthropicLLMs = map[string]bool{
	"claude-instant-1": true,
	"claude-2":         true,
}

var ValidLLMMap = internal.MergeMaps(ValidOpenAILLMs, ValidAnthropicLLMs)

var MaxLLMTokensMap = map[string]int{
	"gpt-3.5-turbo":      4096,
	"gpt-3.5-turbo-16k":  16_385,
	"gpt-3.5-turbo-1106": 16_385,
	"gpt-4":              8192,
	"gpt-4-32k":          32_768,
	"gpt-4-1106-preview": 128_000,
	"claude-instant-1":   100_000,
	"claude-2":           100_000,
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

func NewRetryableHTTPClient(retryMax int, timeout time.Duration) *http.Client {
	leveledLogger := internal.NewLeveledLogrus(log)

	client := retryablehttp.NewClient()
	client.RetryMax = retryMax
	client.HTTPClient.Timeout = timeout
	client.Logger = leveledLogger
	client.Backoff = retryablehttp.DefaultBackoff
	client.CheckRetry = retryPolicy

	httpClient := &http.Client{
		Transport: otelhttp.NewTransport(
			client.StandardClient().Transport,
			otelhttp.WithClientTrace(func(ctx context.Context) *httptrace.ClientTrace {
				return otelhttptrace.NewClientTrace(ctx)
			}),
		),
	}

	return httpClient
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
