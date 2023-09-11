package web

import (
	"context"
	"net/http"
	"strconv"

	"github.com/getzep/zep/pkg/models"
)

func NewUserList(userStore models.UserStore, cursor int64, limit int64) *UserList {
	return &UserList{
		UserStore: userStore,
		Cursor:    cursor,
		Limit:     limit,
	}
}

type UserList struct {
	UserStore  models.UserStore
	Users      []*models.User
	TotalCount int
	Cursor     int64
	Limit      int64
}

func (u *UserList) Next(ctx context.Context) error {
	u.Cursor += u.Limit
	return u.Get(ctx)
}

func (u *UserList) Prev(ctx context.Context) error {
	if u.Cursor-int64(u.Limit) >= 0 {
		u.Cursor -= u.Limit
	} else {
		u.Cursor = 0
	}
	return u.Get(ctx)
}

func (u *UserList) Get(ctx context.Context) error {
	users, err := u.UserStore.ListAll(ctx, u.Cursor, int(u.Limit))
	if err != nil {
		return err
	}
	u.Users = users

	return nil
}

func CreateUserListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cursorStr := r.URL.Query().Get("cursor")
		cursor, _ := strconv.ParseInt(
			cursorStr,
			10,
			64,
		) // safely ignore error, it will be 0 if conversion fails

		var limit int64 = 10
		userList := NewUserList(appState.UserStore, cursor, limit)

		err := userList.Get(r.Context())
		if err != nil {
			log.Errorf("Failed to get user list: %s", err)
			http.Error(w, "Failed to get user list", http.StatusInternalServerError)
			return
		}

		page := NewPage(
			"Users",
			"Users subtitle",
			"/admin/users",
			[]string{
				"templates/pages/users.html",
				"templates/components/content/*.html",
				"templates/components/user_table.html",
			},
			userList,
			nil,
		)

		page.Render(w)
	}
}
