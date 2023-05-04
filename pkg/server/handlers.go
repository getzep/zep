package server

import (
	"context"
	"encoding/json"
	"github.com/go-chi/chi/v5/middleware"
	"net/http"
	"sync"

	"github.com/go-chi/chi/v5"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/models"
)

var log = internal.GetLogger()

// GetMemoryHandler returns a handler for GET requests to /memory/{sessionId}
func GetMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter,
		r *http.Request) {
		requestID := middleware.GetReqID(r.Context())
		if requestID != "" {
			log.Debugf("GetMemoryHandler started for %s", requestID)
		}
		sessionID := chi.URLParam(r, "sessionId")
		sessionMemory, err := appState.MemoryStore.GetMemory(r.Context(), appState,
			sessionID, 0, 0)
		if err != nil {
			log.Errorf("error getting memory: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if err := json.NewEncoder(w).Encode(sessionMemory); err != nil {
			log.Errorf("error encoding memory: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
	}
}

// PostMemoryHandler returns a handler for POST requests to /memory/{sessionId}
func PostMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var memoryMessages models.MessagesAndSummary
		if err := json.NewDecoder(r.Body).Decode(&memoryMessages); err != nil {
			log.Errorf("error decoding posted memory: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		// TODO: remove waitgroup
		wg := sync.WaitGroup{}
		err := appState.MemoryStore.PutMemory(
			r.Context(),
			appState,
			sessionID,
			&memoryMessages,
			&wg,
		)
		if err != nil {
			log.Errorf("error persisting memory: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		appState.MemoryStore.NotifyExtractors(
			context.Background(),
			appState,
			&models.MessageEvent{SessionID: sessionID,
				Messages: memoryMessages.Messages},
		)
	}
}

// DeleteMemoryHandler returns a handler for DELETE requests to /memory/{sessionId}
func DeleteMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")

		err := appState.MemoryStore.DeleteSession(r.Context(), sessionID)
		if err != nil {
			log.Errorf("error deleting memory: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
	}
}

// RunSearchHandler returns a handler for POST requests to /search/{sessionId}
func RunSearchHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload models.SearchPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			log.Errorf("error decoding search payload: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		searchResult, err := appState.MemoryStore.SearchMemory(
			r.Context(),
			appState,
			sessionID,
			&payload,
		)
		if err != nil {
			log.Errorf("error searching memory: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if err := json.NewEncoder(w).Encode(searchResult); err != nil {
			log.Errorf("error encoding search result: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
	}
}
