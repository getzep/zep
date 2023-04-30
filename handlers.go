package main

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/redis/go-redis/v9"
)

func getMemoryHandler(appState *AppState, redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter,
		r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		getMemory(w, r, appState, redisClient, sessionID)
	}
}

func postMemoryHandler(appState *AppState, redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		postMemory(w, r, appState, redisClient, sessionID)
	}
}

func deleteMemoryHandler(redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		deleteMemory(w, r, redisClient, sessionID)
	}
}

func runRetrievalHandler(appState *AppState, redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload SearchPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		runRetrieval(w, r, sessionID, payload, appState, redisClient)
	}
}
