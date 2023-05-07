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

// GetMemoryHandler godoc
// @Summary      Returns a memory (latest summary and list of messages) for a given session
// @Description  get memory by session id
// @Tags         memory
// @Accept       json
// @Produce      json
// @Param        session_id   path      string  true  "Session ID"
// @Param        lastn    query     integer  false  "Last N messages. Overrides memory_window configuration"
// @Success      200  {object}  []models.Memory
// @Failure      404  {object}  APIError "Not Found"
// @Failure      500  {object}  APIError "Internal Server Error"
// @Router       /api/v1/sessions/{sessionId}/memory [get]
func GetMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		lastN, err := extractQueryStringValueToInt(r, "lastn")
		if err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}

		sessionMemory, err := appState.MemoryStore.GetMemory(r.Context(), appState,
			sessionID, lastN)
		if err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := encodeJSON(w, sessionMemory); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// PostMemoryHandler godoc
// @Summary      Add memory messages to a given session
// @Description  add memory messages by session id
// @Tags         memory
// @Accept       json
// @Produce      json
// @Param        session_id   path      string  true  "Session ID"
// @Param        memoryMessages   body    models.Memory   true  "Memory messages"
// @Success      200  {string}  string "OK"
// @Failure      404  {object}  APIError "Not Found"
// @Failure      500  {object}  APIError "Internal Server Error"
// @Router       /api/v1/sessions/{sessionId}/memory [post]
func PostMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var memoryMessages models.Memory
		if err := decodeJSON(r, &memoryMessages); err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}

		if err := appState.MemoryStore.PutMemory(
			r.Context(),
			appState,
			sessionID,
			&memoryMessages,
		); err != nil {
			renderError(w, err, http.StatusInternalServerError)
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

// DeleteMemoryHandler godoc
// @Summary      Delete memory messages for a given session
// @Description  delete memory messages by session id
// @Tags         memory
// @Accept       json
// @Produce      json
// @Param        session_id   path      string  true  "Session ID"
// @Success      200  {string}  string "OK"
// @Failure      404  {object}  APIError "Not Found"
// @Failure      500  {object}  APIError "Internal Server Error"
// @Router       /api/v1/sessions/{sessionId}/memory [delete]
func DeleteMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")

		if err := appState.MemoryStore.DeleteSession(r.Context(), sessionID); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// RunSearchHandler godoc
// @Summary      Search memory messages for a given session
// @Description  search memory messages by session id and query
// @Tags         search
// @Accept       json
// @Produce      json
// @Param        session_id   path      string  true  "Session ID"
// @Param        limit   query     integer  false  "Limit the number of results returned"
// @Param        searchPayload   body    models.SearchPayload   true  "Search query"
// @Success      200  {object}  []models.SearchResult
// @Failure      404  {object}  APIError "Not Found"
// @Failure      500  {object}  APIError "Internal Server Error"
// @Router       /api/v1/sessions/{sessionId}/search [post]
func RunSearchHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload models.SearchPayload
		if err := decodeJSON(r, &payload); err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}
		limit, err := extractQueryStringValueToInt(r, "limit")
		if err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}
		searchResult, err := appState.MemoryStore.SearchMemory(
			r.Context(),
			appState,
			sessionID,
			&payload,
			limit,
		)
		if err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		if err := encodeJSON(w, searchResult); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

func encodeJSON(w http.ResponseWriter, data interface{}) error {
	return json.NewEncoder(w).Encode(data)
}

func decodeJSON(r *http.Request, data interface{}) error {
	return json.NewDecoder(r.Body).Decode(&data)
}

func renderError(w http.ResponseWriter, err error, status int) {
	log.Errorf("error: %v", err)
	http.Error(w, err.Error(), status)
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

type APIError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}
