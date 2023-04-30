package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"

	"github.com/redis/go-redis/v9"
)

func handleGetMemory(
	w http.ResponseWriter,
	r *http.Request,
	appState *AppState,
	redisClient *redis.Client,
	sessionID string,
) {
	conn := redisClient.Conn()
	defer conn.Close()

	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)
	keys := []string{summaryKey, tokenCountKey}

	pipe := redisClient.Pipeline()
	lrangeCmd := pipe.LRange(r.Context(), sessionID, 0, appState.WindowSize-1)
	mgetCmd := pipe.MGet(r.Context(), keys...)
	_, err := pipe.Exec(r.Context())

	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	messages, err := lrangeCmd.Result()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	values, err := mgetCmd.Result()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
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

	w.Header().Set("Content-Type", "application/json")
	err = json.NewEncoder(w).Encode(response)
	if err != nil {
		log.Fatal(err)
	}
}

func handlePostMemory(
	w http.ResponseWriter,
	r *http.Request,
	appState *AppState,
	redisClient *redis.Client,
	sessionID string,
) {

	var memoryMessages MemoryMessagesAndContext
	if err := json.NewDecoder(r.Body).Decode(&memoryMessages); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	conn := redisClient.Conn()
	defer conn.Close()

	messages := make([]string, len(memoryMessages.Messages))
	for i, memoryMessage := range memoryMessages.Messages {
		messages[i] = fmt.Sprintf("%s: %s", memoryMessage.Role, memoryMessage.Content)
	}

	if memoryMessages.Summary != "" {
		_, err := conn.Set(r.Context(), fmt.Sprintf("%s_summary", sessionID), memoryMessages.Summary, 0).
			Result()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
	}

	res, err := conn.LPush(r.Context(), sessionID, messages).Result()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	if appState.LongTermMemory {
		go func() {
			if err := indexMessages(memoryMessages.Messages, sessionID, appState.OpenAIClient, redisClient); err != nil {
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
			if err := handleCompaction(sessionID, appState, redisClient); err != nil {
				log.Printf("Error in handleCompaction: %v\n", err)
			}
		}()
	}

	response := AckResponse{Status: "Ok"}
	w.Header().Set("Content-Type", "application/json")
	err = json.NewEncoder(w).Encode(response)
	if err != nil {
		log.Fatal(err)
	}
}

func handleDeleteMemory(
	w http.ResponseWriter,
	r *http.Request,
	redisClient *redis.Client,
	sessionID string,
) {

	conn := redisClient.Conn()
	defer conn.Close()

	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

	keys := []string{summaryKey, sessionID, tokenCountKey}

	_, err := conn.Del(r.Context(), keys...).Result()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	response := AckResponse{Status: "Ok"}
	w.Header().Set("Content-Type", "application/json")
	err = json.NewEncoder(w).Encode(response)
	if err != nil {
		log.Fatal(err)
	}
}
