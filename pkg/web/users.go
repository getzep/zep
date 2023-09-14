package web

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"

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
	users, err := u.UserStore.ListAll(ctx, u.Cursor, int(u.Limit))
	if err != nil {
		return err
	}

	userRows := make([]*UserRow, len(users))
	for i, user := range users {
		sessions, err := u.UserStore.GetSessions(ctx, user.UserID)
		if err != nil {
			return err
		}
		userRows[i] = &UserRow{
			User:         user,
			SessionCount: len(sessions),
		}
	}

	count, err := u.UserStore.CountAll(ctx)
	if err != nil {
		return err
	}
	u.UserRows = userRows
	u.ListCount = len(users)
	u.TotalCount = int64(count)

	return nil
}

func GetUserListHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cursorStr := r.URL.Query().Get("cursor")
		cursor, _ := strconv.ParseInt(
			cursorStr,
			10,
			64,
		) // safely ignore error, it will be 0 if conversion fails

		renderUserListPage(w, r, appState, cursor, UserListLimit)
	}
}

func renderUserListPage(w http.ResponseWriter, r *http.Request,
	appState *models.AppState, cursor int64, limit int64) {
	const path = "/admin/users"

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
		path,
		[]string{
			"templates/pages/users.html",
			"templates/components/content/*.html",
			"templates/components/user_table.html",
		},
		[]BreadCrumb{
			{
				Title: "Users",
				Path:  path,
			},
		},
		userList,
	)

	page.Render(w, r)
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
			handleError(w, models.NewBadRequestError("user id not provided"), "user id not provided")
			return
		}

		renderUserDetailForm(appState, w, r, userID)
	}
}

func PostUserDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(w, models.NewBadRequestError("user id not provided"), "user id not provided")
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

		renderUserDetailForm(appState, w, r, userID)
	}
}

func renderUserDetailForm(
	appState *models.AppState,
	w http.ResponseWriter,
	r *http.Request,
	userID string,
) {
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

	sessions, err := appState.UserStore.GetSessions(r.Context(), user.UserID)
	if err != nil {
		handleError(w, err, "failed to get user sessions")
		return
	}

	sessionList := &SessionList{
		Sessions: sessions,
		Limit:    0,
		Cursor:   0,
	}

	userData := UserFormData{
		User:           *user,
		MetadataString: string(metadataString),
		SessionList:    sessionList,
	}

	path := "/admin/users/" + user.UserID

	page := NewPage(
		"User Details",
		"View, edit, and delete a user.",
		path,
		[]string{
			"templates/pages/user_details.html",
			"templates/components/content/*.html",
			"templates/components/user_details.html",
			"templates/components/session_table.html",
		},
		[]BreadCrumb{
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

func DeleteUserHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(w, models.NewBadRequestError("user id not provided"), "user id not provided")
			return
		}

		err := appState.UserStore.Delete(r.Context(), userID)
		if err != nil {
			handleError(w, err, "failed to delete user")
			return
		}

		renderUserListPage(w, r, appState, 0, UserListLimit)
	}
}
