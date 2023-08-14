package llms

import (
	"context"
	"fmt"

	"github.com/getzep/zep/pkg/models"

	"github.com/hashicorp/go-retryablehttp"

	"github.com/getzep/zep/config"

	"github.com/getzep/zep/internal"
)

const DefaultTemperature = 0.0
const MaxAPIRequestAttempts = 5
const InvalidLLMModelError = "llm model is not set or is invalid"

var log = internal.GetLogger()

func NewLLMClient(ctx context.Context, cfg *config.Config) (models.ZepLLM, error) {
	switch cfg.LLM.Service {
	case "openai":
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

func NewRetryableHTTPClient() *retryablehttp.Client {
	retryableHttpClient := retryablehttp.NewClient()
	retryableHttpClient.RetryMax = MaxAPIRequestAttempts
	retryableHttpClient.Logger = log

	return retryableHttpClient
}
