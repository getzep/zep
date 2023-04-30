package memory

import (
	"encoding/json"

	"net/http"

	"github.com/danielchalef/zep/pkg/app"
	"github.com/redis/go-redis/v9"
)

func RunRetrieval(
	w http.ResponseWriter,
	r *http.Request,
	sessionID string,
	payload SearchPayload,
	state *app.AppState,
	redisClient *redis.Client,
) {
	if !state.LongTermMemory {
		http.Error(w, "Long term memory is disabled", http.StatusBadRequest)
		return
	}

	openAIClient := state.OpenAIClient

	results, err := SearchMessages(payload.Text, sessionID, openAIClient, redisClient)
	if err != nil {
		log.Error("Error Retrieval API: %v\n", err)
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	jsonResponse, err := json.Marshal(results)
	if err != nil {
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, err = w.Write(jsonResponse)
	if err != nil {
		log.Error(err)
	}
}
