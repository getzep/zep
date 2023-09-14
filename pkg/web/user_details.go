package web

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/getzep/zep/pkg/models"
)

type UserFormData struct {
	models.User
	MetadataString string
	*SessionList
}

func GetUserDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(w, errors.New("user id not provided"), "user id not provided")
			return
		}

		renderUserDetailForm(appState, w, r, userID)
	}
}

func PostUserDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			handleError(w, errors.New("user id not provided"), "user id not provided")
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
