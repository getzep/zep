package webhandlers

import (
	"context"
	"net/http"

	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

var SessionTableColumns = []web.Column{
	{
		Name:       "Session",
		Sortable:   true,
		OrderByKey: "session_id",
	},
	{
		Name:       "User",
		Sortable:   true,
		OrderByKey: "user_id",
	},
	{
		Name:       "Created",
		Sortable:   true,
		OrderByKey: "created_at",
	},
}

func NewSessionList(
	memoryStore models.MemoryStore[*bun.DB], r *http.Request, userID string,
) *SessionList {
	// if we have a userID, the columns are not sortable
	if userID != "" {
		for i := range SessionTableColumns {
			SessionTableColumns[i].Sortable = false
		}
	}
	t := web.NewTable("session-table", SessionTableColumns)
	s := &SessionList{
		MemoryStore: memoryStore,
		UserID:      userID,
		Table:       t,
	}
	s.ParseQueryParams(r)
	return s
}

type SessionList struct {
	MemoryStore models.MemoryStore[*bun.DB]
	UserID      string
	*web.Table
}

func (sl *SessionList) Get(ctx context.Context, appState *models.AppState) error {
	var sr *models.SessionListResponse
	if sl.UserID == "" {
		var err error
		sr, err = sl.MemoryStore.ListSessionsOrdered(
			ctx,
			sl.CurrentPage,
			sl.PageSize,
			sl.OrderBy,
			sl.Asc,
		)
		if err != nil {
			return err
		}
	} else {
		sessions, err := appState.UserStore.GetSessions(
			ctx,
			sl.UserID,
		)
		if err != nil {
			return err
		}
		sr = &models.SessionListResponse{
			Sessions:   sessions,
			RowCount:   len(sessions),
			TotalCount: len(sessions),
		}
	}
	sl.Rows = sr.Sessions
	sl.RowCount = sr.RowCount
	sl.TotalCount = sr.TotalCount
	sl.Offset = sl.GetOffset()
	sl.PageCount = sl.GetPageCount()

	return nil
}

func GetSessionListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sl := NewSessionList(appState.MemoryStore, r, "")
		if err := sl.Get(r.Context(), appState); err != nil {
			handleError(w, err, "failed to get session list")
			return
		}

		path := sl.GetTablePath("/admin/sessions")
		page := web.NewPage(
			"Sessions",
			"View and delete sessions",
			path,
			[]string{
				"templates/pages/sessions.html",
				"templates/components/content/*.html",
				"templates/components/session_table.html",
			},
			[]web.BreadCrumb{
				{
					Title: "Sessions",
					Path:  path,
				},
			},
			sl,
		)

		page.Render(w, r)
	}
}
