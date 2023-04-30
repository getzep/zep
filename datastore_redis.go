package main

import (
	"context"
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"

	"github.com/redis/go-redis/v9"
)

var _ DataStore[*redis.Client] = &RedisDataStore{}

type RedisDataStore struct {
	BaseDataStore[*redis.Client]
}

func NewRedisDataStore(client *redis.Client) *RedisDataStore {
	return &RedisDataStore{BaseDataStore[*redis.Client]{client: client}}
}

func (rds *RedisDataStore) GetMemory(
	ctx context.Context,
	appState *AppState,
	sessionID string,
) (*MemoryResponse, error) {
	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)
	keys := []string{summaryKey, tokenCountKey}

	pipe := rds.client.Pipeline()
	lrangeCmd := pipe.LRange(ctx, sessionID, 0, appState.WindowSize-1)
	mgetCmd := pipe.MGet(ctx, keys...)
	_, err := pipe.Exec(ctx)

	if err != nil {
		return nil, err
	}

	messages, err := lrangeCmd.Result()
	if err != nil {
		return nil, err
	}

	values, err := mgetCmd.Result()
	if err != nil {
		return nil, err
	}

	summary, _ := values[0].(string)
	tokensString, _ := values[1].(string)
	tokens, _ := strconv.ParseInt(tokensString, 10, 64)

	memoryMessages := make([]MemoryMessage, len(messages))
	for i, message := range messages {
		parts := strings.SplitN(message, ": ", 2)
		if len(parts) == 2 {
			memoryMessages[i] = MemoryMessage{
				Role:    parts[0],
				Content: parts[1],
			}
		}
	}

	response := MemoryResponse{
		Messages: memoryMessages,
		Summary:  summary,
		Tokens:   tokens,
	}

	return &response, nil
}

func (rds *RedisDataStore) PostMemory(
	ctx context.Context,
	appState *AppState,
	sessionID string,
	memoryMessages MemoryMessagesAndContext,
) error {
	messages := make([]string, len(memoryMessages.Messages))
	for i, memoryMessage := range memoryMessages.Messages {
		messages[i] = fmt.Sprintf("%s: %s", memoryMessage.Role, memoryMessage.Content)
	}

	if memoryMessages.Summary != "" {
		_, err := rds.client.Set(ctx, fmt.Sprintf("%s_summary", sessionID), memoryMessages.Summary, 0).
			Result()
		if err != nil {
			return err
		}
	}

	res, err := rds.client.LPush(ctx, sessionID, messages).Result()
	if err != nil {
		return err
	}

	if appState.LongTermMemory {
		go func() {
			if err := indexMessages(memoryMessages.Messages, sessionID, appState.OpenAIClient, rds.client); err != nil {
				log.Printf("Error in indexMessages: %v\n", err)
			}
		}()
	}

	if res > appState.WindowSize {
		sessionCleanup, _ := appState.SessionCleanup.LoadOrStore(sessionID, &sync.Mutex{})
		sessionCleanupMutex := sessionCleanup.(*sync.Mutex)
		sessionCleanupMutex.Lock()

		go func() {
			defer sessionCleanupMutex.Unlock()

			log.Println("running compact")
			if err := handleCompaction(sessionID, appState, rds.client); err != nil {
				log.Printf("Error in handleCompaction: %v\n", err)
			}
		}()
	}

	return nil
}

func (rds *RedisDataStore) DeleteMemory(ctx context.Context, sessionID string) error {
	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

	keys := []string{summaryKey, sessionID, tokenCountKey}

	_, err := rds.client.Del(ctx, keys...).Result()
	if err != nil {
		return err
	}

	return nil
}
