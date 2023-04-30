package reducers

import (
	"context"
	"fmt"
	"log"
	"strings"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/app"
	"github.com/pkoukk/tiktoken-go"
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

	promptData := SummaryPromptTemplateData{
		PrevSummary:    prevSummary,
		MessagesJoined: messagesJoined,
	}

	progressivePrompt, err := internal.ParsePrompt(summaryPromptTemplate, promptData)
	if err != nil {
		return "", 0, err
	}

	req := openai.ChatCompletionRequest{
		Model:     openai.GPT3Dot5Turbo,
		MaxTokens: 512,
		Messages: []openai.ChatCompletionMessage{
			{
				Role:    openai.ChatMessageRoleUser,
				Content: progressivePrompt,
			},
		},
		Temperature: 0.0,
	}

	ctx := context.Background()
	resp, err := openAIClient.CreateChatCompletion(ctx, req)
	if err != nil {
		return "", 0, err
	}

	completion := resp.Choices[0].Message.Content
	tokensUsed := resp.Usage.TotalTokens

	return completion, tokensUsed, nil
}

func HandleCompaction(sessionID string, appState *app.AppState, redisConn *redis.Client) error {
	half := appState.WindowSize / 2
	summaryKey := fmt.Sprintf("%s_summary", sessionID)

	ctx := context.Background()

	pipe := redisConn.Pipeline()
	lrangeCmd := pipe.LRange(ctx, sessionID, half, appState.WindowSize)
	getCmd := pipe.Get(ctx, summaryKey)

	_, err := pipe.Exec(ctx)
	if err != nil && err != redis.Nil {
		return &app.ZepError{RedisError: err}
	}

	var messages []string
	err = lrangeCmd.ScanSlice(&messages)
	if err != nil {
		return &app.ZepError{RedisError: err}
	}

	res, err := getCmd.Result()
	if err != nil && err != redis.Nil {
		return &app.ZepError{RedisError: err}
	}

	var summary *string
	if res != "" {
		summary = &res
	}

	maxTokens := 4096
	summaryMaxTokens := 512
	bufferTokens := 230
	maxMessageTokens := maxTokens - summaryMaxTokens - bufferTokens

	totalTokens := 0
	var tempMessages []string
	totalTokensTemp := 0

	for _, message := range messages {
		messageTokensUsed := getTokenCount(message)

		if totalTokensTemp+messageTokensUsed <= maxMessageTokens {
			tempMessages = append(tempMessages, message)
			totalTokensTemp += messageTokensUsed
		} else {
			newSummary, summaryTokensUsed, err := incrementalSummarization(appState.OpenAIClient, summary, tempMessages)
			if err != nil {
				return &app.ZepError{IncrementalSummarizationError: err.Error()}
			}

			totalTokens += summaryTokensUsed
			summary = &newSummary
			tempMessages = []string{message}
			totalTokensTemp = messageTokensUsed
		}
	}

	if len(tempMessages) > 0 {
		newSummary, summaryTokensUsed,
			err := incrementalSummarization(
			appState.OpenAIClient,
			summary,
			tempMessages,
		)
		if err != nil {
			return &app.ZepError{IncrementalSummarizationError: err.Error()}
		}

		totalTokens += summaryTokensUsed
		summary = &newSummary
	}

	if summary != nil {
		newContext := *summary
		tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

		err = executePipelinedCommands(
			ctx,
			half,
			sessionID,
			summaryKey,
			tokenCountKey,
			newContext,
			int64(totalTokens),
			redisConn,
		)
		if err != nil {
			return &app.ZepError{RedisError: err}
		}
	} else {
		return &app.ZepError{IncrementalSummarizationError: "No context found after summarization"}
	}

	return nil
}

func executePipelinedCommands(
	ctx context.Context,
	half int64,
	sessionID, summaryKey, tokenCountKey,
	newContext string,
	tokensUsed int64,
	redisConn *redis.Client,
) error {
	_, err := redisConn.Pipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.LTrim(ctx, sessionID, 0, int64(half))
		pipe.Set(ctx, summaryKey, newContext, 0)
		pipe.IncrBy(ctx, tokenCountKey, int64(tokensUsed))
		return nil
	})
	return err
}

func reverseSlice(slice []string) {
	for i, x := range slice[:len(slice)/2] {
		opp := len(slice) - 1 - i
		slice[i], slice[opp] = slice[opp], x
	}
}

func getTokenCount(text string) int {
	encoding := "cl100k_base"
	tkm, err := tiktoken.GetEncoding(encoding)
	if err != nil {
		log.Fatal(err)
	}

	return len(tkm.Encode(text, nil, nil))
}
