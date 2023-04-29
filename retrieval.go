package main

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/redis/go-redis/v9"
)

func handleRunRetrieval(
	w http.ResponseWriter,
	r *http.Request,
	sessionID string,
	payload SearchPayload,
	state *AppState,
	redisClient *redis.Client,
) {
	if !state.LongTermMemory {
		http.Error(w, "Long term memory is disabled", http.StatusBadRequest)
		return
	}

	openAIClient := state.OpenAIClient

	results, err := searchMessages(payload.Text, sessionID, openAIClient, redisClient)
	if err != nil {
		log.Printf("Error Retrieval API: %v\n", err)
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
		log.Fatal(err)
	}
}
