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
	sessionID string,
	pageNumber int,
	pageSize int,
) *MessageList {
	return &MessageList{
		MemoryStore: memoryStore,
		SessionID:   sessionID,
		PageNumber:  pageNumber,
		PageSize:    pageSize,
	}
}

type MessageList struct {
	MemoryStore models.MemoryStore[*bun.DB]
	SessionID   string
	Messages    []models.Message
	TotalCount  int
	PageNumber  int
	PageSize    int
	Offset      int
}

func (m *MessageList) Get(ctx context.Context, appState *models.AppState) error {
	messages, err := m.MemoryStore.GetMessageList(ctx, appState, m.SessionID, m.PageNumber, m.PageSize)
	if err != nil {
		return err
	}
	if messages == nil {
		return errors.New("failed to get message list")
	}
	m.Messages = messages.Messages
	m.TotalCount = messages.TotalCount

	return nil
}

func GetSessionDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionID")
		if sessionID == "" {
			handleError(w, errors.New("user id not provided"), "user id not provided")
			return
		}

		pageNumberStr := r.URL.Query().Get("page")
		pageNumber, _ := strconv.ParseInt(
			pageNumberStr,
			10,
			32,
		) // safely ignore error, it will be 0 if conversion fails

		var pageSize = 10
		if pageNumber == 0 {
			pageNumber = 1
		}
		messageList := NewMessageList(appState.MemoryStore, sessionID, int(pageNumber), pageSize)

		err := messageList.Get(r.Context(), appState)
		if err != nil {
			log.Errorf("Failed to get user list: %s", err)
			http.Error(w, "Failed to get user list", http.StatusInternalServerError)
			return
		}

		messageList.Offset = (int(pageNumber)-1)*pageSize + 1

		page := NewPage(
			"Session Details",
			sessionID,
			"/admin/sessions/"+sessionID,
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
