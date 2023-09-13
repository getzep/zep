package web

import (
	"context"
	"errors"
	"net/http"
	"strconv"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
	"github.com/uptrace/bun"
)

func NewMessageList(
	memoryStore models.MemoryStore[*bun.DB],
	SessionID string,
	cursor int64,
	limit int64,
) *MessageList {
	return &MessageList{
		MemoryStore: memoryStore,
		SessionID:   SessionID,
		Cursor:      cursor,
		Limit:       limit,
	}
}

type MessageList struct {
	MemoryStore models.MemoryStore[*bun.DB]
	SessionID   string
	Messages    []models.Message
	TotalCount  int
	Cursor      int64
	Limit       int64
}

func (m *MessageList) Get(ctx context.Context, appState *models.AppState) error {
	messages, err := m.MemoryStore.GetMessageList(ctx, appState, m.SessionID, m.Cursor, int(m.Limit))
	if err != nil {
		return err
	}
	m.Messages = messages

	return nil
}

func GetSessionDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionID")
		if sessionID == "" {
			handleError(w, errors.New("user id not provided"), "user id not provided")
			return
		}

		cursorStr := r.URL.Query().Get("cursor")
		cursor, _ := strconv.ParseInt(
			cursorStr,
			10,
			64,
		) // safely ignore error, it will be 0 if conversion fails

		var limit int64 = 10
		messageList := NewMessageList(appState.MemoryStore, sessionID, cursor, limit)

		err := messageList.Get(r.Context(), appState)
		if err != nil {
			log.Errorf("Failed to get user list: %s", err)
			http.Error(w, "Failed to get user list", http.StatusInternalServerError)
			return
		}

		page := NewPage(
			"Session Details",
			sessionID,
			"/admin/messages",
			[]string{
				"templates/pages/session_details.html",
				"templates/components/content/*.html",
				"templates/components/chat_history.html",
			},
			messageList,
		)

		page.Render(w, r)
	}
}
