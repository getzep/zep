package routes

import (
	"encoding/json"
	"net/http"

	"github.com/danielchalef/zep/pkg/app"
	"github.com/danielchalef/zep/pkg/memory"
	"github.com/go-chi/chi/v5"
	"github.com/redis/go-redis/v9"
)

func GetMemoryHandler(appState *app.AppState, redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter,
		r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		memory.GetMemory(w, r, appState, redisClient, sessionID)
	}
}

func PostMemoryHandler(appState *app.AppState, redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		memory.PostMemory(w, r, appState, redisClient, sessionID)
	}
}

func DeleteMemoryHandler(redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		memory.DeleteMemory(w, r, redisClient, sessionID)
	}
}

func RunRetrievalHandler(appState *app.AppState, redisClient *redis.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload memory.SearchPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		memory.RunRetrieval(w, r, sessionID, payload, appState, redisClient)
	}
}
