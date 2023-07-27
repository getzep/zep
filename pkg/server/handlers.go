package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

var log = internal.GetLogger()

const OKResponse = "OK"

// GetMemoryHandler godoc
//
//	@Summary		Returns a memory (latest summary and list of messages) for a given session
//	@Description	get memory by session id
//	@Tags			memory
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string	true	"Session ID"
//	@Param			lastn		query		integer	false	"Last N messages. Overrides memory_window configuration"
//	@Success		200			{object}	[]models.Memory
//	@Failure		404			{object}	APIError	"Not Found"
//	@Failure		500			{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/sessions/{sessionId}/memory [get]
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
		if sessionMemory == nil || sessionMemory.Messages == nil {
			renderError(w, fmt.Errorf("not found"), http.StatusNotFound)
			return
		}

		if err := encodeJSON(w, sessionMemory); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetSessionHandler godoc
//
//	@Summary		Returns a session by ID
//	@Description	get session by id
//	@Tags			session
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string	true	"Session ID"
//	@Success		200			{object}	models.Session
//	@Failure		404			{object}	APIError	"Not Found"
//	@Failure		500			{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/sessions/{sessionId} [get]
func GetSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")

		session, err := appState.MemoryStore.GetSession(r.Context(), appState, sessionID)
		if err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		if session == nil {
			renderError(w, fmt.Errorf("not found"), http.StatusNotFound)
			return
		}

		if err := encodeJSON(w, session); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// PostSessionHandler godoc
//
//	@Summary		Add a session
//	@Description	add session by id
//	@Tags			session
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string			true	"Session ID"
//	@Param			session		body		models.Session	true	"Session"
//	@Success		200			{string}	string			"OK"
//	@Failure		400			{object}	APIError		"Bad Request"
//	@failure		500			{object}	APIError		"Internal Server Error"
//	@Router			/api/v1/sessions/{sessionId} [post]
func PostSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var session models.Session
		if err := decodeJSON(r, &session); err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}
		// If session ID is not provided, use the one from the URL
		// If session ID is provided, make sure it matches the one from the URL
		if session.SessionID != "" && session.SessionID != sessionID {
			renderError(
				w,
				fmt.Errorf("session ID mismatch: %s != %s", session.SessionID, sessionID),
				http.StatusBadRequest,
			)
			return
		}
		session.SessionID = sessionID

		if err := appState.MemoryStore.PutSession(r.Context(), appState, &session); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(OKResponse))
	}
}

// PostMemoryHandler godoc
//
//	@Summary		Add memory messages to a given session
//	@Description	add memory messages by session id
//	@Tags			memory
//	@Accept			json
//	@Produce		json
//	@Param			sessionId		path		string			true	"Session ID"
//	@Param			memoryMessages	body		models.Memory	true	"Memory messages"
//	@Success		200				{string}	string			"OK"
//	@Failure		404				{object}	APIError		"Not Found"
//	@Failure		500				{object}	APIError		"Internal Server Error"
//	@Router			/api/v1/sessions/{sessionId}/memory [post]
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
			false,
		); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(OKResponse))
	}
}

// DeleteMemoryHandler godoc
//
//	@Summary		Delete memory messages for a given session
//	@Description	delete memory messages by session id
//	@Tags			memory
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string		true	"Session ID"
//	@Success		200			{string}	string		"OK"
//	@Failure		404			{object}	APIError	"Not Found"
//	@Failure		500			{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/sessions/{sessionId}/memory [delete]
func DeleteMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")

		if err := appState.MemoryStore.DeleteSession(r.Context(), sessionID); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(OKResponse))
	}
}

// SearchMemoryHandler godoc
//
//	@Summary		Search memory messages for a given session
//	@Description	search memory messages by session id and query
//	@Tags			search
//	@Accept			json
//	@Produce		json
//	@Param			sessionId		path		string						true	"Session ID"
//	@Param			limit			query		integer						false	"Limit the number of results returned"
//	@Param			searchPayload	body		models.MemorySearchPayload	true	"Search query"
//	@Success		200				{object}	[]models.MemorySearchResult
//	@Failure		404				{object}	APIError	"Not Found"
//	@Failure		500				{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/sessions/{sessionId}/search [post]
func SearchMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload models.MemorySearchPayload
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
		if searchResult == nil {
			renderError(w, fmt.Errorf("not found"), http.StatusNotFound)
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
	if status != http.StatusNotFound {
		// Don't log not found errors
		log.Error(err)
	}
	if strings.Contains(err.Error(), "is deleted") {
		status = http.StatusBadRequest
	}
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

// APIError represents an error response. Used for swagger documentation.
type APIError struct {
	Message string `json:"message"`
}
