package apihandlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/getzep/zep/api/apidata"
	"github.com/getzep/zep/api/handlertools"
	"github.com/getzep/zep/lib/observability"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

const defaultMessageLimit = 100

// UpdateMessageMetadataHandler Updates the metadata of a message.
//
//	@Summary			Updates the metadata of a message.
//	@Description		Updates the metadata of a message.
//	@Tags				messages
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string							true	"The ID of the session."
//	@Param				messageUUID	path		string							true	"The UUID of the message."
//	@Param				body		body		models.MessageMetadataUpdate	true	"The metadata to update."
//	@Success			200			{object}	apidata.Message					"The updated message."
//	@Failure			404			{object}	apidata.APIError				"Not Found"
//	@Failure			500			{object}	apidata.APIError				"Internal Server Error"
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions/{sessionId}/messages/{messageUUID} [patch]
func UpdateMessageMetadataHandler(as *models.AppState) http.HandlerFunc {
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

		messageUUID := handlertools.UUIDFromURL(r, w, "messageUUID")
		if messageUUID == uuid.Nil {
			handlertools.LogAndRenderError(w, zerrors.NewBadRequestError("messageUUID is required"), http.StatusBadRequest)
			return
		}

		var messageUpdate models.MessageMetadataUpdate

		err = json.NewDecoder(r.Body).Decode(&messageUpdate)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		message := models.Message{
			UUID:     messageUUID,
			Metadata: messageUpdate.Metadata,
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Messages,
			"update_message_metadata",
			map[string]any{
				"message_uuid": messageUUID,
			},
		)

		err = rs.Memories.UpdateMessages(
			r.Context(), sessionID, []models.Message{message}, false, false,
		)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		messages, err := rs.Memories.GetMessagesByUUID(r.Context(), sessionID, []uuid.UUID{messageUUID})
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		resp := apidata.MessageTransformer(messages[0])

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetMessageHandler retrieves a specific message.
//
//	@Summary			Gets a specific message from a session
//	@Description		Gets a specific message from a session
//	@Tags				messages
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string				true	"The ID of the session."
//	@Param				messageUUID	path		string				true	"The UUID of the message."
//	@Success			200			{object}	apidata.Message		"The message."
//	@Failure			404			{object}	apidata.APIError	"Not Found"
//	@Failure			500			{object}	apidata.APIError	"Internal Server Error"
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions/{sessionId}/messages/{messageUUID} [get]
func GetMessageHandler(as *models.AppState) http.HandlerFunc {
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

		messageUUID := handlertools.UUIDFromURL(r, w, "messageUUID")
		messageIDs := []uuid.UUID{messageUUID}

		observability.I().CaptureBreadcrumb(
			observability.Category_Messages,
			"get_message",
			map[string]any{
				"message_uuid": messageUUID,
			},
		)

		messages, err := rs.Memories.GetMessagesByUUID(r.Context(), sessionID, messageIDs)
		if err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		if len(messages) == 0 {
			handlertools.LogAndRenderError(w, fmt.Errorf("no message found for UUID"), http.StatusNotFound)
			return
		}

		resp := apidata.MessageTransformer(messages[0])

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetMessagesForSessionHandler retrieves all messages for a specific session.
//
//	@Summary			Lists messages for a session
//	@Description		Lists messages for a session, specified by limit and cursor.
//	@Tags				messages
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string	true	"Session ID"
//	@Param				limit		query		integer	false	"Limit the number of results returned"
//	@Param				cursor		query		int64	false	"Cursor for pagination"
//	@Success			200			{object}	apidata.MessageListResponse
//	@Failure			404			{object}	apidata.APIError	"Not Found"
//	@Failure			500			{object}	apidata.APIError	"Internal Server Error"
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions/{sessionId}/messages [get]
func GetMessagesForSessionHandler(as *models.AppState) http.HandlerFunc {
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

		limit, err := handlertools.IntFromQuery[int](r, "limit")
		if err != nil {
			limit = defaultMessageLimit
		}

		cursor, err := handlertools.IntFromQuery[int](r, "cursor")
		if err != nil {
			cursor = 1
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Messages,
			"get_messages_for_session",
			map[string]any{
				"cursor": cursor,
				"limit":  limit,
			},
		)

		messages, err := rs.Memories.GetMessageList(r.Context(), sessionID, cursor, limit)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.MessageListResponse{
			Messages:   apidata.MessageListTransformer(messages.Messages),
			TotalCount: messages.TotalCount,
			RowCount:   messages.RowCount,
		}

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}
