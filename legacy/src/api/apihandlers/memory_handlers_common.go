package apihandlers

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/getzep/zep/api/apidata"
	"github.com/getzep/zep/api/handlertools"
	"github.com/getzep/zep/lib/observability"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

const (
	maxMessagesPerMemory = 30
	maxMessageLength     = 2500
	maxLongMessageLength = 100_000
	DefaultLastNMessages = 6
)

// GetMemoryHandler godoc
//
//	@Summary			Get session memory
//	@Description		Returns a memory (latest summary, list of messages and facts) for a given session
//	@Tags				memory
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string	true	"The ID of the session for which to retrieve memory."
//	@Param				lastn		query		integer	false	"The number of most recent memory entries to retrieve."
//	@Param				minRating	query		float64	false	"The minimum rating by which to filter facts"
//	@Success			200			{object}	apidata.Memory
//	@Failure			404			{object}	apidata.APIError	"Not Found"
//	@Failure			500			{object}	apidata.APIError	"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//
//	@Router				/sessions/{sessionId}/memory [get]
func GetMemoryHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		sessionID, err := url.PathUnescape(chi.URLParam(r, "sessionId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		lastN, err := handlertools.IntFromQuery[int](r, "lastn")
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		if lastN < 0 {
			handlertools.LogAndRenderError(w, fmt.Errorf("lastn cannot be negative"), http.StatusBadRequest)
			return
		}

		memoryOptions, err := extractMemoryFilterOptions(r)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		// if lastN is 0, use the project settings memory window
		if lastN == 0 {
			lastN = DefaultLastNMessages
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"get_memory",
			map[string]any{
				"last_n": lastN,
			},
		)

		sessionMemory, err := rs.Memories.GetMemory(r.Context(), sessionID, lastN, memoryOptions...)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		if sessionMemory == nil || sessionMemory.Messages == nil {
			handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
			return
		}

		resp := apidata.MemoryTransformer(sessionMemory)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// PostMemoryHandler godoc
//
//	@Summary			Add memory to the specified session.
//	@Description		Add memory to the specified session.
//	@Tags				memory
//	@Accept				json
//	@Produce			json
//	@Param				sessionId		path		string						true	"The ID of the session to which memory should be added."
//	@Param				memoryMessages	body		apidata.AddMemoryRequest	true	"A Memory object representing the memory messages to be added."
//	@Success			200				{object}	apidata.SuccessResponse		"OK"
//	@Failure			500				{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//
//	@Router				/sessions/{sessionId}/memory [post]
func PostMemoryHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		sessionID, err := url.PathUnescape(chi.URLParam(r, "sessionId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		var memoryMessages apidata.AddMemoryRequest
		if err = handlertools.DecodeJSON(r, &memoryMessages); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		if len(memoryMessages.Messages) > maxMessagesPerMemory {
			maxMemoryError := fmt.Errorf(
				"max messages per memory of %d exceeded. reduce the number of messages in your request",
				maxMessagesPerMemory,
			)
			handlertools.LogAndRenderError(w, maxMemoryError, http.StatusBadRequest)
		}

		l := maxMessageLength
		if !rs.EnablementProfile.Plan.IsFree() {
			l = maxLongMessageLength
		}

		for i := range memoryMessages.Messages {
			if utf8.RuneCountInString(memoryMessages.Messages[i].Content) > l {
				err := fmt.Errorf("message content exceeds %d characters", l)
				handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
				return
			}
		}

		for i := range memoryMessages.Messages {
			if memoryMessages.Messages[i].RoleType == "" {
				handlertools.LogAndRenderError(w, fmt.Errorf("messages are required to have a RoleType"), http.StatusBadRequest)
				return
			}
		}

		if err := handlertools.Validate.Struct(memoryMessages); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"post_memory",
		)

		if err := putMemory(r, rs, sessionID, memoryMessages); err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		handlertools.JSONOK(w, http.StatusCreated)
	}
}

// DeleteMemoryHandler godoc
//
//	@Summary			Delete memory messages for a given session
//	@Description		delete memory messages by session id
//	@Tags				memory
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string					true	"The ID of the session for which memory should be deleted."
//	@Success			200			{object}	apidata.SuccessResponse	"OK"
//	@Failure			404			{object}	apidata.APIError		"Not Found"
//	@Failure			500			{object}	apidata.APIError		"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//
//	@Router				/sessions/{sessionId}/memory [delete]
func DeleteMemoryHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		sessionID, err := url.PathUnescape(chi.URLParam(r, "sessionId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"delete_memory",
		)
		if err := deleteMemory(r.Context(), sessionID, rs); err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		handlertools.JSONOK(w, http.StatusOK)
	}
}
