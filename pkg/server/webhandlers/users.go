package webhandlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
)

var UserTableColumns = []web.Column{
	{
		Name:       "User",
		Sortable:   true,
		OrderByKey: "user_id",
	},
	{
		Name:       "Email",
		Sortable:   true,
		OrderByKey: "email",
	},
	{
		Name:       "Sessions",
		Sortable:   false,
		OrderByKey: "session_count",
	},
	{
		Name:       "Created",
		Sortable:   true,
		OrderByKey: "created_at",
	},
}

type UserRow struct {
	*models.User
	SessionCount int
}

func NewUserList(userStore models.UserStore, r *http.Request) *UserList {
	t := web.NewTable("user-table", UserTableColumns)
	t.ParseQueryParams(r)
	return &UserList{
		UserStore: userStore,
		Table:     t,
	}
}

type UserList struct {
	UserStore models.UserStore
	*web.Table
}

func (u *UserList) Get(ctx context.Context) error {
	var userRows []*UserRow

	ur, err := u.UserStore.ListAllOrdered(ctx, u.CurrentPage, u.PageSize, u.OrderBy, u.Asc)
	if err != nil {
		return err
	}

	userRows = make([]*UserRow, len(ur.Users))
	for i, user := range ur.Users {
		sessions, err := u.UserStore.GetSessions(ctx, user.UserID)
		if err != nil {
			return err
		}
		userRows[i] = &UserRow{
			User:         user,
			SessionCount: len(sessions),
		}
	}

	u.Rows = userRows
	u.RowCount = ur.RowCount
	u.TotalCount = ur.TotalCount
	u.PageCount = u.GetPageCount()
	u.Offset = u.GetOffset()

	log.Debugf("user list: %+v", u.Table)

	return nil
}

func GetUserListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ul := NewUserList(appState.UserStore, r)
		if err := ul.Get(r.Context()); err != nil {
			handleError(w, err, "failed to get user list")
			return
		}

		path := ul.GetTablePath("/admin/users")
		page := web.NewPage(
			"Users",
			"View, edit, and delete users",
			path,
			[]string{
				"templates/pages/users.html",
				"templates/components/content/*.html",
				"templates/components/user_table.html",
			},
			[]web.BreadCrumb{
				{
					Title: "Users",
					Path:  path,
				},
			},
			ul,
		)

		page.Render(w, r)
	}
}

type UserFormData struct {
	models.User
	MetadataString string
	*SessionList
}

func GetUserDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(
				w,
				models.NewBadRequestError("user id not provided"),
				"user id not provided",
			)
			return
		}

		user, err := appState.UserStore.Get(r.Context(), userID)
		if err != nil {
			handleError(w, err, "failed to get user")
			return
		}

		var metadataString = ""
		if len(user.Metadata) != 0 {
			metadataBytes, err := json.Marshal(user.Metadata)
			if err != nil {
				handleError(w, err, "failed to marshal user metadata")
				return
			}
			metadataString = string(metadataBytes)
		}

		sl := NewSessionList(appState.MemoryStore, r, userID)
		if err := sl.Get(r.Context(), appState); err != nil {
			handleError(w, err, "failed to get session list")
			return
		}

		userData := UserFormData{
			User:           *user,
			MetadataString: metadataString,
			SessionList:    sl,
		}

		path := sl.GetTablePath("/admin/users/" + user.UserID)

		page := web.NewPage(
			user.UserID,
			fmt.Sprintf("%s %s", user.FirstName, user.LastName),
			path,
			[]string{
				"templates/pages/user_details.html",
				"templates/components/content/*.html",
				"templates/components/user_details.html",
				"templates/components/session_table.html",
			},
			[]web.BreadCrumb{
				{
					Title: "Users",
					Path:  "/admin/users",
				},
				{
					Title: user.UserID,
					Path:  path,
				},
			},
			userData,
		)

		page.Render(w, r)
	}
}

func PostUserDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(
				w,
				models.NewBadRequestError("user id not provided"),
				"user id not provided",
			)
			return
		}

		if err := r.ParseForm(); err != nil {
			handleError(w, err, "failed to parse form")
			return
		}

		var metadata map[string]interface{}
		if len(r.PostForm.Get("metadata")) != 0 {
			if err := json.Unmarshal([]byte(r.FormValue("metadata")), &metadata); err != nil {
				handleError(w, err, "failed to unmarshal metadata")
				return
			}
		}

		user := models.UpdateUserRequest{
			UserID:    userID,
			Email:     r.PostForm.Get("email"),
			FirstName: r.PostForm.Get("first_name"),
			LastName:  r.PostForm.Get("last_name"),
			Metadata:  metadata,
		}

		_, err := appState.UserStore.Update(r.Context(), &user, true)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handleError(w, err, fmt.Sprintf("user %s not found", userID))
				return
			}
			handleError(w, err, "failed to update user")
			return
		}

		GetUserDetailsHandler(appState)(w, r)
	}
}

func DeleteUserHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(
				w,
				models.NewBadRequestError("user id not provided"),
				"user id not provided",
			)
			return
		}

		err := appState.UserStore.Delete(r.Context(), userID)
		if err != nil {
			handleError(w, err, "failed to delete user")
			return
		}

		GetUserListHandler(appState)(w, r)
	}
}
