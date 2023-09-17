package webhandlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"

	"github.com/getzep/zep/pkg/server/handlertools"
	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
)

const UserListLimit int64 = 10

type UserRow struct {
	*models.User
	SessionCount int
}

func NewUserList(userStore models.UserStore, cursor int64, limit int64) *UserList {
	return &UserList{
		UserStore: userStore,
		Cursor:    cursor,
		Limit:     limit,
	}
}

type UserList struct {
	UserStore  models.UserStore
	UserRows   []*UserRow
	TotalCount int64
	ListCount  int
	Cursor     int64
	Limit      int64
}

func (u *UserList) Get(ctx context.Context) error {
	var wg sync.WaitGroup
	var users []*models.User
	var count int
	var userRows []*UserRow
	var mu sync.Mutex
	var firstErr error

	users, err := u.UserStore.ListAll(ctx, u.Cursor, int(u.Limit))
	if err != nil {
		return err
	}

	wg.Add(2)
	go func() {
		defer wg.Done()
		userRows = make([]*UserRow, len(users))
		for i, user := range users {
			sessions, err := u.UserStore.GetSessions(ctx, user.UserID)
			if err != nil {
				mu.Lock()
				firstErr = err
				mu.Unlock()
				return
			}
			userRows[i] = &UserRow{
				User:         user,
				SessionCount: len(sessions),
			}
		}
	}()

	go func() {
		defer wg.Done()
		count, err = u.UserStore.CountAll(ctx)
		if err != nil {
			mu.Lock()
			firstErr = err
			mu.Unlock()
			return
		}
	}()

	wg.Wait()

	if firstErr != nil {
		return firstErr
	}

	u.UserRows = userRows
	u.ListCount = len(users)
	u.TotalCount = int64(count)

	return nil
}

func GetUserListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cursor, err := handlertools.IntFromQuery[int64](r, "cursor")

		const path = "/admin/users"
		userList := NewUserList(appState.UserStore, cursor, UserListLimit)

		err = userList.Get(r.Context())
		if err != nil {
			log.Errorf("Failed to get user list: %s", err)
			http.Error(w, "Failed to get user list", http.StatusInternalServerError)
			return
		}

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
			userList,
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

		sl := NewSessionList(appState.MemoryStore, r)

		userData := UserFormData{
			User:           *user,
			MetadataString: string(metadataString),
			SessionList:    sl,
		}

		path := "/admin/users/" + user.UserID

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
