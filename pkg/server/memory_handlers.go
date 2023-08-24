package server

import (
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
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
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId}/memory [get]
func GetMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		lastN, err := extractQueryStringValueToInt[int](r, "lastn")
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
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId} [get]
func GetSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")

		session, err := appState.MemoryStore.GetSession(r.Context(), appState, sessionID)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				renderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			renderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := encodeJSON(w, session); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// CreateSessionHandler godoc
//
//	@Summary		Add a session
//	@Description	add session by id
//	@Tags			session
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string						true	"Session ID"
//	@Param			session		body		models.CreateSessionRequest	true	"Session"
//	@Success		200			{string}	string						"OK"
//	@Failure		400			{object}	APIError					"Bad Request"
//	@failure		500			{object}	APIError					"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId} [post]
func CreateSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var session models.CreateSessionRequest
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

		_, err := appState.MemoryStore.CreateSession(r.Context(), appState, &session)
		if err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(OKResponse))
	}
}

// UpdateSessionHandler godoc
//
//	@Summary		Add a session
//	@Description	add session by id
//	@Tags			session
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string						true	"Session ID"
//	@Param			session		body		models.SessionUpdateRequest	true	"Session"
//	@Success		200			{string}	string						"OK"
//	@Failure		400			{object}	APIError					"Bad Request"
//	@Failure		404			{object}	APIError					"Not Found"
//	@failure		500			{object}	APIError					"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId} [post]
func UpdateSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var session models.SessionUpdateRequest
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

		err := appState.MemoryStore.UpdateSession(r.Context(), appState, &session)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				renderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(OKResponse))
	}
}

// GetSessionListHandler godoc
//
//	@Summary		Returns all sessions
//	@Description	get all sessions with optional limit and cursor for pagination
//	@Tags			session
//	@Accept			json
//	@Produce		json
//	@Param			limit	query		integer	false	"Limit the number of results returned"
//	@Param			cursor	query		int64	false	"Cursor for pagination (Unix timestamp)"
//	@Success		200		{array}		[]models.Session
//	@Failure		400		{object}	APIError	"Bad Request"
//	@Failure		500		{object}	APIError	"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/sessions [get]
func GetSessionListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var limit int
		var err error
		if limit, err = extractQueryStringValueToInt[int](r, "limit"); err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}
		var cursorTime int64
		if cursorTime, err = extractQueryStringValueToInt[int64](r, "cursor"); err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}
		cursor := time.Unix(cursorTime, 0)
		sessions, err := appState.MemoryStore.ListSessions(r.Context(), appState, cursor, limit)
		if err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
		if err := encodeJSON(w, sessions); err != nil {
			renderError(w, err, http.StatusInternalServerError)
			return
		}
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
//	@Security		Bearer
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
//	@Security		Bearer
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
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId}/search [post]
func SearchMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var payload models.MemorySearchPayload
		if err := decodeJSON(r, &payload); err != nil {
			renderError(w, err, http.StatusBadRequest)
			return
		}
		limit, err := extractQueryStringValueToInt[int](r, "limit")
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
