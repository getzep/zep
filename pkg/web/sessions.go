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
	ListCount   int
	Cursor      int64
	Limit       int64
}

func (u *SessionList) Get(ctx context.Context, appState *models.AppState) error {
	sessions, err := u.MemoryStore.ListSessions(ctx, appState, u.Cursor, int(u.Limit))
	if err != nil {
		return err
	}
	u.Sessions = sessions
	u.ListCount = len(sessions)

	count, err := u.MemoryStore.CountSessions(ctx, appState)
	if err != nil {
		handleError(nil, err, "failed to get session count")
		return err
	}
	u.TotalCount = count

	return nil
}

func GetSessionListHandler(appState *models.AppState) http.HandlerFunc {
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
			handleError(w, err, "failed to get session list")
			return
		}

		path := "/admin/sessions"

		page := NewPage(
			"Sessions",
			"View and delete sessions",
			path,
			[]string{
				"templates/pages/sessions.html",
				"templates/components/content/*.html",
				"templates/components/session_table.html",
			},
			[]BreadCrumb{
				{
					Title: "Sessions",
					Path:  path,
				},
			},
			sessionList,
		)

		page.Render(w, r)
	}
}
