package apihandlers

import (
	"errors"
	"fmt"
	"net/http"

	"github.com/getzep/zep/pkg/server/handlertools"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
)

// CreateUserHandler godoc
//
//	@Summary		Add a user
//	@Description	add user by id
//	@Tags			user
//	@Accept			json
//	@Produce		json
//	@Param			user	body		models.CreateUserRequest	true	"User"
//	@Success		201		{object}	models.User
//	@Failure		400		{object}	APIError	"Bad Request"
//	@Failure		500		{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/user [post]
func CreateUserHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var user models.CreateUserRequest
		if err := handlertools.DecodeJSON(r, &user); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		createdUser, err := appState.UserStore.Create(r.Context(), &user)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusCreated)
		if err := handlertools.EncodeJSON(w, createdUser); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetUserHandler godoc
//
//	@Summary		Returns a user by ID
//	@Description	get user by id
//	@Tags			user
//	@Accept			json
//	@Produce		json
//	@Param			userId	path		string	true	"User ID"
//	@Success		200		{object}	models.User
//	@Failure		404		{object}	APIError	"Not Found"
//	@Failure		500		{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/user/{userId} [get]
func GetUserHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userId := chi.URLParam(r, "userId")

		user, err := appState.UserStore.Get(r.Context(), userId)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, user); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// UpdateUserHandler godoc
//
//	@Summary		Update a user
//	@Description	update user by id
//	@Tags			user
//	@Accept			json
//	@Produce		json
//	@Param			userId	path		string						true	"User ID"
//	@Param			user	body		models.UpdateUserRequest	true	"User"
//	@Success		200		{object}	models.User
//	@Failure		400		{object}	APIError	"Bad Request"
//	@Failure		404		{object}	APIError	"Not Found"
//	@Failure		500		{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/user/{userId} [patch]
func UpdateUserHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userId")
		var user models.UpdateUserRequest
		if err := handlertools.DecodeJSON(r, &user); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		user.UserID = userID

		updatedUser, err := appState.UserStore.Update(r.Context(), &user, true)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, updatedUser); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// DeleteUserHandler godoc
//
//	@Summary		Delete a user
//	@Description	delete user by id
//	@Tags			user
//	@Accept			json
//	@Produce		json
//	@Param			userId	path		string		true	"User ID"
//	@Success		200		{string}	string		"OK"
//	@Failure		404		{object}	APIError	"Not Found"
//	@Failure		500		{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/user/{userId} [delete]
func DeleteUserHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userId")

		if err := appState.UserStore.Delete(r.Context(), userID); err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		_, _ = w.Write([]byte(OKResponse))
	}
}

// ListAllUsersHandler godoc
//
//	@Summary		List all users
//	@Description	list all users with pagination
//	@Tags			user
//	@Accept			json
//	@Produce		json
//	@Param			limit	query		int				false	"Limit"
//	@Param			cursor	query		int64			false	"Cursor"
//	@Success		200		{array}		[]models.User	"Successfully retrieved list of users"
//	@Failure		400		{object}	APIError		"Bad Request"
//	@Failure		500		{object}	APIError		"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/user [get]
func ListAllUsersHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		limit, err := handlertools.IntFromQuery[int](r, "limit")
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		cursor, err := handlertools.IntFromQuery[int64](r, "cursor")
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		users, err := appState.UserStore.ListAll(r.Context(), cursor, limit)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, users); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// ListUserSessionsHandler godoc
//
//	@Summary		List all sessions for a user
//	@Description	list all sessions for a user by user id
//	@Tags			user
//	@Accept			json
//	@Produce		json
//	@Param			userId	path		string	true	"User ID"
//	@Success		200		{array}		models.Session
//	@Failure		500		{object}	APIError	"Internal Server Error"
//	@Security		Bearer
//	@Router			/api/v1/user/{userId}/sessions [get]
func ListUserSessionsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		userID := chi.URLParam(r, "userId")

		sessions, err := appState.UserStore.GetSessions(r.Context(), userID)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, sessions); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}
