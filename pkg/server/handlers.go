package server

import (
	"context"
	"encoding/json"
	"github.com/go-chi/chi/v5"
	"net/http"
	"strconv"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/models"
)

var log = internal.GetLogger()

// GetMemoryHandler returns a handler for GET requests to /memory/{sessionId}
// lastn is an optional query string parameter that limits the number of results returned and
// overrides the configured memory_window
func GetMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		lastN, err := extractQueryStringValueToInt(r, "lastn")
		if err != nil {
			http.Error(w, "Invalid lastn parameter", http.StatusBadRequest)
		}
		sessionMemory, err := appState.MemoryStore.GetMemory(r.Context(), appState,
			sessionID, lastN)
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
		var memoryMessages models.Memory
		if err := json.NewDecoder(r.Body).Decode(&memoryMessages); err != nil {
			log.Errorf("error decoding posted memory: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		err := appState.MemoryStore.PutMemory(
			r.Context(),
			appState,
			sessionID,
			&memoryMessages,
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
// limit is an optional query string parameter that limits the number of results returned
func RunSearchHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload models.SearchPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			log.Errorf("error decoding search payload: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		limit, err := extractQueryStringValueToInt(r, "limit")
		if err != nil {
			http.Error(w, "Invalid limit parameter", http.StatusBadRequest)
		}
		searchResult, err := appState.MemoryStore.SearchMemory(
			r.Context(),
			appState,
			sessionID,
			&payload,
			limit,
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

// extractQueryStringValueToInt extracts a query string value and converts it to an int
func extractQueryStringValueToInt(
	r *http.Request,
	param string,
) (int, error) {
	p := r.URL.Query().Get(param)
	var pInt int
	if p != "" {
		var err error
		pInt, err = strconv.Atoi(p)
		if err != nil {
			return 0, err
		}
	}
	return pInt, nil
}
