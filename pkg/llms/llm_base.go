package llms

import (
	"fmt"

	"github.com/getzep/zep/internal"
)

const DefaultTemperature = 0.0

var log = internal.GetLogger()

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

var MaxLLMTokensMap = map[string]int{
	"gpt-3.5-turbo": 4096,
	"gpt-4":         8192,
}

var ValidLLMMap = map[string]bool{
	"gpt-3.5-turbo": true,
	"gpt-4":         true,
}
