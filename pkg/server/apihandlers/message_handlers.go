package apihandlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	log "github.com/sirupsen/logrus"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/server/handlertools"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

const DefaultMessageLimit = 100

// UpdateMessageMetadataHandler updates the metadata of a specific message.
//
// This function handles HTTP PATCH requests at the /api/v1/session/{sessionId}/message/{messageId} endpoint.
// It uses the session ID and message ID provided in the URL to find the specific message.
// The new metadata is provided in the request body as a JSON object.
//
// The function updates the message's metadata with the new metadata and saves the updated message back to the database.
// It then responds with the updated message as a JSON object.
//
//	@Summary		Updates the metadata of a specific message
//	@Description	update message metadata by session id and message id
//	@Tags			messages
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string			true	"Session ID"
//	@Param			messageId	path		string			true	"Message ID"
//	@Param			body		body		models.Message	true	"New Metadata"
//	@Success		200			{object}	models.Message
//	@Failure		404			{object}	APIError	"Not Found"
//	@Failure		500			{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/session/{sessionId}/message/{messageId} [patch]
func UpdateMessageMetadataHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		messageUUID := handlertools.UUIDFromURL(r, w, "messageId")

		log.Debugf("UpdateMessageMetadataHandler - SessionId %s - MessageUUID %s", sessionID, messageUUID)

		message := models.Message{}
		message.UUID = messageUUID
		err := json.NewDecoder(r.Body).Decode(&message)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		err = appState.MemoryStore.UpdateMessages(r.Context(), sessionID, []models.Message{message}, false, false)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			} else {
				handlertools.RenderError(w, err, http.StatusInternalServerError)
				return
			}
		}

		messages, err := appState.MemoryStore.GetMessagesByUUID(r.Context(), sessionID, []uuid.UUID{messageUUID})
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			} else {
				handlertools.RenderError(w, err, http.StatusInternalServerError)
				return
			}
		}

		if err := handlertools.EncodeJSON(w, messages[0]); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetMessageHandler retrieves a specific message.
//
// This function handles HTTP GET requests at the /api/v1/session/{sessionId}/message/{messageId} endpoint.
// It uses the session ID and message ID provided in the URL to find the specific message.
//
// The function responds with the found message as a JSON object.
// If the session ID or message ID does not exist, the function responds with a 404 Not Found status code.
// If there is an error while fetching the message, the function responds with a 500 Internal Server Error status code.
//
//	@Summary		Retrieves a specific message
//	@Description	get message by session id and message id
//	@Tags			messages
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string	true	"Session ID"
//	@Param			messageId	path		string	true	"Message ID"
//	@Success		200			{object}	models.Message
//	@Failure		404			{object}	APIError	"Not Found"
//	@Failure		500			{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/session/{sessionId}/message/{messageId} [get]
func GetMessageHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")
		messageUUID := handlertools.UUIDFromURL(r, w, "messageId")
		log.Debugf("GetMessageHandler: sessionID: %s, messageID: %s", sessionID, messageUUID)

		messageIDs := []uuid.UUID{messageUUID}
		messages, err := appState.MemoryStore.GetMessagesByUUID(r.Context(), sessionID, messageIDs)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			} else {
				handlertools.RenderError(w, err, http.StatusInternalServerError)
				return
			}
		}

		if err := handlertools.EncodeJSON(w, messages[0]); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetMessagesForSessionHandler retrieves all messages for a specific session.
//
// This function handles HTTP GET requests at the /api/v1/session/{sessionId}/messages endpoint.
// It uses the session ID provided in the URL to fetch all messages associated with that session.
//
// The function responds with a JSON array of messages. Each message in the array includes its ID, content, and metadata.
// If the session ID does not exist, the function responds with a 404 Not Found status code.
// If there is an error while fetching the messages, the function responds with a 500 Internal Server Error status code.
//
//	@Summary		Retrieves all messages for a specific session
//	@Description	get messages by session id
//	@Tags			messages
//	@Accept			json
//	@Produce		json
//	@Param			sessionId	path		string	true	"Session ID"
//	@Success		200			{array}		models.Message
//	@Failure		404			{object}	APIError	"Not Found"
//	@Failure		500			{object}	APIError	"Internal Server Error"
//	@Router			/api/v1/session/{sessionId}/messages [get]
func GetMessagesForSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionId")

		var limit int
		var err error
		if limit, err = handlertools.IntFromQuery[int](r, "limit"); err != nil {
			limit = DefaultMessageLimit
		}

		var cursor int
		if cursor, err = handlertools.IntFromQuery[int](r, "cursor"); err != nil {
			cursor = 1
		}

		log.Debugf("GetMessagesForSessionHandler - SessionId %s Limit %d Cursor %d", sessionID, limit, cursor)

		messages, err := appState.MemoryStore.GetMessageList(r.Context(), sessionID, cursor, limit)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, messages); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}
