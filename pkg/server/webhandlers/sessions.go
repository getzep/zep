package webhandlers

import (
	"context"
	"net/http"

	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

const SessionPageSize = 10

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
	memoryStore models.MemoryStore[*bun.DB], r *http.Request,
) *SessionList {
	s := &SessionList{
		MemoryStore: memoryStore,
		Table: &web.Table{
			ID:      "session-table",
			Columns: SessionTableColumns,
		},
	}
	s.ParseQueryParams(r)
	return s
}

type SessionList struct {
	MemoryStore models.MemoryStore[*bun.DB]
	*web.Table
}

func (sl *SessionList) Get(ctx context.Context, appState *models.AppState) error {
	sessionResponse, err := sl.MemoryStore.ListSessionsOrdered(
		ctx,
		appState,
		sl.CurrentPage,
		sl.PageSize,
		sl.OrderBy,
		sl.Asc,
	)
	if err != nil {
		return err
	}
	sl.Rows = sessionResponse.Sessions
	sl.RowCount = sessionResponse.ResponseCount
	sl.TotalCount = sessionResponse.TotalCount
	sl.Offset = sl.GetOffset()
	sl.PageCount = sl.GetPageCount()

	return nil
}

func GetSessionListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sl := NewSessionList(appState.MemoryStore, r)

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
