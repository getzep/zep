package memory

import (
	"encoding/json"

	"net/http"

	"github.com/danielchalef/zep/pkg/app"
	"github.com/redis/go-redis/v9"
)

func RunRetrieval(
	httpWriter http.ResponseWriter,
	sessionID string,
	payload SearchPayload,
	state *app.AppState,
	redisClient *redis.Client,
) {
	if !state.LongTermMemory {
		http.Error(httpWriter, "Long term memory is disabled", http.StatusBadRequest)
		return
	}

	openAIClient := state.OpenAIClient

	results, err := SearchMessages(payload.Text, sessionID, openAIClient, redisClient)
	if err != nil {
		log.Error("Error Retrieval API: %v\n", err)
		http.Error(httpWriter, "Internal server error", http.StatusInternalServerError)
		return
	}

	jsonResponse, err := json.Marshal(results)
	if err != nil {
		http.Error(httpWriter, "Internal server error", http.StatusInternalServerError)
		return
	}

	httpWriter.Header().Set("Content-Type", "application/json")
	httpWriter.WriteHeader(http.StatusOK)
	_, err = httpWriter.Write(jsonResponse)
	if err != nil {
		log.Error(err)
	}
}
