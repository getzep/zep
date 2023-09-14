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

func NewSessionDetails(
	memoryStore models.MemoryStore[*bun.DB],
	sessionID string,
	pageNumber int,
	pageSize int,
) *SessionDetails {
	return &SessionDetails{
		MemoryStore: memoryStore,
		SessionID:   sessionID,
		PageNumber:  pageNumber,
		PageSize:    pageSize,
	}
}

type SessionDetails struct {
	MemoryStore models.MemoryStore[*bun.DB]
	SessionID   string
	Session     *models.Session
	Messages    []models.Message
	TotalCount  int
	PageNumber  int
	PageSize    int
	Offset      int
}

func (m *SessionDetails) Get(ctx context.Context, appState *models.AppState) error {
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

		userID := chi.URLParam(r, "userID")

		// Get Messages
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
		sessionDetails := NewSessionDetails(appState.MemoryStore, sessionID, int(pageNumber), pageSize)

		err := sessionDetails.Get(r.Context(), appState)
		if err != nil {
			handleError(w, err, "failed to get message list")
			return
		}

		sessionDetails.Offset = (int(pageNumber)-1)*pageSize + 1

		// Get Session Details
		session, err := appState.MemoryStore.GetSession(r.Context(), appState, sessionID)
		if err != nil {
			handleError(w, err, "failed to get session")
			return
		}
		sessionDetails.Session = session

		var breadCrumbs []BreadCrumb
		if len(userID) == 0 {
			breadCrumbs = []BreadCrumb{
				{
					Title: "Sessions",
					Path:  "/admin/sessions",
				},
				{
					Title: sessionID,
					Path:  "/admin/sessions/" + sessionID,
				},
			}
		} else {
			breadCrumbs = []BreadCrumb{
				{
					Title: "Users",
					Path:  "/admin/users",
				},
				{
					Title: userID,
					Path:  "/admin/users/" + userID,
				},
				{
					Title: sessionID,
					Path:  "/admin/users/" + userID + "/sessions/" + sessionID,
				},
			}
		}

		page := NewPage(
			"Session Details",
			"View session information and related messages",
			"/admin/sessions/"+sessionID,
			[]string{
				"templates/pages/session_details.html",
				"templates/components/content/*.html",
				"templates/components/chat_history.html",
			},
			breadCrumbs,
			sessionDetails,
		)

		page.Render(w, r)
	}
}
