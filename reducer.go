package main

import (
	"context"
	"fmt"
	"strings"

	"github.com/redis/go-redis/v9"
	openai "github.com/sashabaranov/go-openai"
)

func incrementalSummarization(
	openAIClient *openai.Client,
	currentSummary *string,
	messages []string,
) (string, int, error) {

	reverseSlice(messages)
	messagesJoined := strings.Join(messages, "\n")
	prevSummary := ""
	if currentSummary != nil {
		prevSummary = *currentSummary
	}

	promptData := ProgressivePromptTemplateData{
		PrevSummary:    prevSummary,
		MessagesJoined: messagesJoined,
	}

	progressivePrompt, err := parsePrompt(progressivePromptTemplate, promptData)
	if err != nil {
		return "", 0, err
	}

	req := openai.CompletionRequest{
		Model:       openai.GPT3Dot5Turbo,
		MaxTokens:   512, // Change the max tokens as needed
		Prompt:      progressivePrompt,
		Temperature: 0.0,
	}

	var ctx = context.Background()
	resp, err := openAIClient.CreateCompletion(ctx, req)
	if err != nil {
		return "", 0, err
	}

	completion := resp.Choices[0].Text

	tokensUsed := resp.Usage.TotalTokens

	return completion, tokensUsed, nil
}

func handleCompaction(sessionID string, stateClone *AppState, redisConn *redis.Client) error {
	half := stateClone.WindowSize / 2
	contextKey := fmt.Sprintf("%s_context", sessionID)

	var messages []string

	ctx := context.Background()

	cmds, err := redisConn.Pipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.LRange(ctx, sessionID, half, stateClone.WindowSize)
		pipe.Get(ctx, contextKey)
		return nil
	})

	if err != nil {
		return &PapyrusError{RedisError: err}
	}

	err = cmds[0].(*redis.StringSliceCmd).ScanSlice(&messages)
	if err != nil {
		return &PapyrusError{RedisError: err}
	}

	res, err := cmds[1].(*redis.StringCmd).Result()
	if err != nil {
		return &PapyrusError{RedisError: err}
	}

	newContext, tokensUsed, err := incrementalSummarization(stateClone.OpenAIClient, &res, messages)
	if err != nil {
		return &PapyrusError{IncrementalSummarizationError: err.Error()}
	}

	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

	_, err = redisConn.Pipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.LTrim(ctx, sessionID, 0, int64(half))
		pipe.Set(ctx, contextKey, newContext, 0)
		pipe.IncrBy(ctx, tokenCountKey, int64(tokensUsed))
		return nil
	})
	if err != nil {
		return &PapyrusError{RedisError: err}
	}

	return nil
}

func reverseSlice(slice []string) {
	for i, x := range slice[:len(slice)/2] {
		opp := len(slice) - 1 - i
		slice[i], slice[opp] = slice[opp], x
	}
}
