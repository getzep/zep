package web

import (
	"encoding/json"
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
			log.Error("user id not provided")
			http.Error(w, "user id not provided", http.StatusInternalServerError)
			return
		}

		renderUserDetailForm(appState, w, r, userID)
	}
}

func PostUserDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userID")
		if userID == "" {
			log.Error("user id not provided")
			http.Error(w, "user id not provided", http.StatusInternalServerError)
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
		log.Errorf("failed to get user: %s", err)
		http.Error(w, "failed to get user", http.StatusInternalServerError)
		return
	}

	var metadataString = ""
	if len(user.Metadata) != 0 {
		metadataBytes, err := json.Marshal(user.Metadata)
		if err != nil {
			log.Errorf("failed to marshal user metadata: %s", err)
			http.Error(w, "failed to marshal user metadata", http.StatusInternalServerError)
			return
		}
		metadataString = string(metadataBytes)
	}

	sessions, err := appState.UserStore.GetSessions(r.Context(), user.UserID)
	if err != nil {
		log.Errorf("failed to get user sessions: %s", err)
		http.Error(w, "failed to get user sessions", http.StatusInternalServerError)
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

	page := NewPage(
		"User Details",
		"View, edit, and delete a user.",
		"/admin/users/"+user.UserID,
		[]string{
			"templates/pages/user_details.html",
			"templates/components/content/*.html",
			"templates/components/user_details.html",
			"templates/components/session_table.html",
		},
		userData,
	)

	page.Render(w, r)
}
