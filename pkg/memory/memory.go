package memory

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/app"
	"github.com/danielchalef/zep/pkg/reducers"
	"github.com/redis/go-redis/v9"
)

var log = internal.GetLogger()

func GetMemory(
	httpWriter http.ResponseWriter,
	httpRequest *http.Request,
	appState *app.AppState,
	redisClient *redis.Client,
	sessionID string,
) {
	conn := redisClient.Conn()
	defer conn.Close()

	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)
	keys := []string{summaryKey, tokenCountKey}

	pipe := redisClient.Pipeline()
	lrangeCmd := pipe.LRange(httpRequest.Context(), sessionID, 0, appState.WindowSize-1)
	mgetCmd := pipe.MGet(httpRequest.Context(), keys...)
	_, err := pipe.Exec(httpRequest.Context())

	if err != nil {
		log.Error(err)
		http.Error(httpWriter, err.Error(), http.StatusInternalServerError)
		return
	}

	messages, err := lrangeCmd.Result()
	if err != nil {
		log.Error(err)
		http.Error(httpWriter, err.Error(), http.StatusInternalServerError)
		return
	}

	values, err := mgetCmd.Result()
	if err != nil {
		log.Error(err)
		http.Error(httpWriter, err.Error(), http.StatusInternalServerError)
		return
	}

	summary, _ := values[0].(string)
	tokensString, _ := values[1].(string)
	tokens, _ := strconv.ParseInt(tokensString, 10, 64)

	memoryMessages := make([]Message, len(messages))
	for i, message := range messages {
		parts := strings.SplitN(message, ": ", 2)
		if len(parts) == 2 {
			memoryMessages[i] = Message{
				Role:    parts[0],
				Content: parts[1],
			}
		}
	}

	response := Response{
		Messages: memoryMessages,
		Summary:  summary,
		Tokens:   tokens,
	}

	httpWriter.Header().Set("Content-Type", "application/json")
	err = json.NewEncoder(httpWriter).Encode(response)
	if err != nil {
		log.Error(err)
	}
}

func PostMemory(
	httpWriter http.ResponseWriter,
	httpRequest *http.Request,
	appState *app.AppState,
	redisClient *redis.Client,
	sessionID string,
) {
	var memoryMessages MessagesAndSummary
	if err := json.NewDecoder(httpRequest.Body).Decode(&memoryMessages); err != nil {
		log.Error(err)
		http.Error(httpWriter, err.Error(), http.StatusBadRequest)
		return
	}

	conn := redisClient.Conn()
	defer conn.Close()

	messages := make([]string, len(memoryMessages.Messages))
	for i, memoryMessage := range memoryMessages.Messages {
		messages[i] = fmt.Sprintf("%s: %s", memoryMessage.Role, memoryMessage.Content)
	}

	if memoryMessages.Summary != "" {
		_, err := conn.Set(httpRequest.Context(), fmt.Sprintf("%s_summary", sessionID), memoryMessages.Summary, 0).
			Result()
		if err != nil {
			log.Error(err)
			http.Error(httpWriter, err.Error(), http.StatusInternalServerError)
			return
		}
	}

	res, err := conn.LPush(httpRequest.Context(), sessionID, messages).Result()
	if err != nil {
		log.Error(err)
		http.Error(httpWriter, err.Error(), http.StatusInternalServerError)
		return
	}

	if appState.LongTermMemory {
		go func() {
			if err := IndexMessages(memoryMessages.Messages, sessionID, appState.OpenAIClient, redisClient); err != nil {
				log.Error("Error in IndexMessages: %v\n", err)
			}
		}()
	}

	if res > appState.WindowSize {
		sessionCleanup, _ := appState.SessionCleanup.LoadOrStore(sessionID, &sync.Mutex{})
		sessionCleanupMutex := sessionCleanup.(*sync.Mutex)
		sessionCleanupMutex.Lock()

		go func() {
			defer sessionCleanupMutex.Unlock()

			log.Info("running compact")
			if err := reducers.HandleCompaction(sessionID, appState, redisClient); err != nil {
				log.Error("Error in handleCompaction: %v\n", err)
			}
		}()
	}

	response := AckResponse{Status: "Ok"}
	httpWriter.Header().Set("Content-Type", "application/json")
	err = json.NewEncoder(httpWriter).Encode(response)
	if err != nil {
		log.Error(err)
	}
}

func DeleteMemory(
	httpWriter http.ResponseWriter,
	httpRequest *http.Request,
	redisClient *redis.Client,
	sessionID string,
) {
	conn := redisClient.Conn()
	defer conn.Close()

	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

	keys := []string{summaryKey, sessionID, tokenCountKey}

	_, err := conn.Del(httpRequest.Context(), keys...).Result()
	if err != nil {
		log.Error(err)
		http.Error(httpWriter, err.Error(), http.StatusInternalServerError)
		return
	}

	response := AckResponse{Status: "Ok"}
	httpWriter.Header().Set("Content-Type", "application/json")
	err = json.NewEncoder(httpWriter).Encode(response)
	if err != nil {
		log.Error(err)
	}
}
