package apihandlers

import (
	"errors"
	"fmt"
	"net/http"

	"github.com/getzep/zep/pkg/server/handlertools"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
)

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
		lastN, err := handlertools.IntFromQuery[int](r, "lastn")
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		sessionMemory, err := appState.MemoryStore.GetMemory(r.Context(), sessionID, lastN)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
		if sessionMemory == nil || sessionMemory.Messages == nil {
			handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
			return
		}

		if err := handlertools.EncodeJSON(w, sessionMemory); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
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

		session, err := appState.MemoryStore.GetSession(r.Context(), sessionID)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, session); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
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
//	@Param			session	body		models.CreateSessionRequest	true	"Session"
//	@Success		201		{object}	models.Session
//	@Failure		400		{object}	APIError	"Bad Request"
//	@failure		500		{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/sessions [post]
func CreateSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var session models.CreateSessionRequest
		if err := handlertools.DecodeJSON(r, &session); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		newSession, err := appState.MemoryStore.CreateSession(r.Context(), &session)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusCreated)
		if err := handlertools.EncodeJSON(w, newSession); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
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
//	@Param			session		body		models.UpdateSessionRequest	true	"Session"
//	@Success		200			{object}	models.Session
//	@Failure		400			{object}	APIError	"Bad Request"
//	@Failure		404			{object}	APIError	"Not Found"
//	@failure		500			{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId} [patch]
func UpdateSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var session models.UpdateSessionRequest
		if err := handlertools.DecodeJSON(r, &session); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}
		session.SessionID = sessionID

		updatedSession, err := appState.MemoryStore.UpdateSession(r.Context(), &session)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
		if err := handlertools.EncodeJSON(w, updatedSession); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
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
//	@Param			cursor	query		int64	false	"Cursor for pagination"
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
		if limit, err = handlertools.IntFromQuery[int](r, "limit"); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}
		var cursor int64
		if cursor, err = handlertools.IntFromQuery[int64](r, "cursor"); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}
		sessions, err := appState.MemoryStore.ListSessions(r.Context(), cursor, limit)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
		if err := handlertools.EncodeJSON(w, sessions); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
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
//	@Failure		500				{object}	APIError		"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/sessions/{sessionId}/memory [post]
func PostMemoryHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		var memoryMessages models.Memory
		if err := handlertools.DecodeJSON(r, &memoryMessages); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		if err := appState.MemoryStore.PutMemory(
			r.Context(),
			sessionID,
			&memoryMessages,
			false,
		); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
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
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
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
		if err := handlertools.DecodeJSON(r, &payload); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}
		limit, err := handlertools.IntFromQuery[int](r, "limit")
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}
		searchResult, err := appState.MemoryStore.SearchMemory(
			r.Context(),
			sessionID,
			&payload,
			limit,
		)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
		if err := handlertools.EncodeJSON(w, searchResult); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}
