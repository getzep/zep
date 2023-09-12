package web

import (
	"context"
	"net/http"
	"strconv"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

func NewSessionList(
	memoryStore models.MemoryStore[*bun.DB],
	cursor int64,
	limit int64,
) *SessionList {
	return &SessionList{
		MemoryStore: memoryStore,
		Cursor:      cursor,
		Limit:       limit,
	}
}

type SessionList struct {
	MemoryStore models.MemoryStore[*bun.DB]
	Sessions    []*models.Session
	TotalCount  int
	Cursor      int64
	Limit       int64
}

func (u *SessionList) Get(ctx context.Context, appState *models.AppState) error {
	sessions, err := u.MemoryStore.ListSessions(ctx, appState, u.Cursor, int(u.Limit))
	if err != nil {
		return err
	}
	u.Sessions = sessions

	return nil
}

func CreateSessionListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cursorStr := r.URL.Query().Get("cursor")
		cursor, _ := strconv.ParseInt(
			cursorStr,
			10,
			64,
		) // safely ignore error, it will be 0 if conversion fails

		var limit int64 = 10
		sessionList := NewSessionList(appState.MemoryStore, cursor, limit)

		err := sessionList.Get(r.Context(), appState)
		if err != nil {
			log.Errorf("Failed to get user list: %s", err)
			http.Error(w, "Failed to get user list", http.StatusInternalServerError)
			return
		}

		page := NewPage(
			"Sessions",
			"Sessions subtitle",
			"/admin/sessions",
			[]string{
				"templates/pages/sessions.html",
				"templates/components/content/*.html",
				"templates/components/session_table.html",
			},
			sessionList,
			nil,
		)

		page.Render(w)
	}
}