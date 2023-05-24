package openairetryclient

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/getzep/zep/internal"

	"github.com/avast/retry-go/v4"
	"github.com/sashabaranov/go-openai"
)

var log = internal.GetLogger()

type OpenAIRetryClient struct {
	openai.Client
	Config struct {
		Timeout     time.Duration
		MaxAttempts uint
	}
}

func (c *OpenAIRetryClient) CreateChatCompletionWithRetry(
	ctx context.Context,
	request openai.ChatCompletionRequest,
) (*openai.ChatCompletionResponse, error) {
	fn := func(ctx context.Context, arg interface{}) (interface{}, error) {
		req := arg.(openai.ChatCompletionRequest)
		return c.CreateChatCompletion(ctx, req)
	}

	result, err := c.retryFunction(ctx, c.Config.Timeout, c.Config.MaxAttempts, fn, request)
	if err != nil {
		return nil, fmt.Errorf("unexpected response from OpenAI API: %w", err)
	}

	response, ok := result.(openai.ChatCompletionResponse)
	if !ok {
		return nil, errors.New(
			"unexpected type returned from openai client CreateChatCompletion",
		)
	}
	return &response, nil
}

func (c *OpenAIRetryClient) CreateEmbeddingsWithRetry(
	ctx context.Context,
	request openai.EmbeddingRequest,
) (*openai.EmbeddingResponse, error) {
	fn := func(ctx context.Context, arg interface{}) (interface{}, error) {
		req := arg.(openai.EmbeddingRequest)
		return c.CreateEmbeddings(ctx, req)
	}

	result, err := c.retryFunction(ctx, c.Config.Timeout, c.Config.MaxAttempts, fn, request)
	if err != nil {
		return nil, fmt.Errorf("unexpected response from OpenAI API: %w", err)
	}

	response, ok := result.(openai.EmbeddingResponse)
	if !ok {
		return nil, errors.New("unexpected type returned from openai client CreateEmbeddings")
	}
	return &response, nil
}

func (c *OpenAIRetryClient) retryFunction(
	ctx context.Context,
	timeout time.Duration,
	maxAttempts uint,
	fn func(context.Context, interface{}) (interface{}, error),
	arg interface{}) (interface{}, error) {
	var result interface{}
	var err error
	retryCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	err = retry.Do(
		func() error {
			result, err = fn(retryCtx, arg)
			return err
		},
		retry.Attempts(maxAttempts),
		retry.Context(retryCtx),
		retry.DelayType(retry.BackOffDelay),
		retry.OnRetry(func(n uint, err error) {
			log.Warningf("Retrying function attempt #%d: %s\n", n, err)
		}),
	)

	if err != nil {
		return nil, err
	}

	return result, nil
}
