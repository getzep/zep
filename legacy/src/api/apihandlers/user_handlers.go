package apihandlers

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"

	"github.com/go-chi/chi/v5"

	"github.com/getzep/zep/api/apidata"
	"github.com/getzep/zep/api/handlertools"
	"github.com/getzep/zep/lib/observability"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

// CreateUserHandler godoc
//
//	@Summary			Add a user.
//	@Description		Add a user.
//	@Tags				user
//	@Accept				json
//	@Produce			json
//	@Param				user	body		models.CreateUserRequest	true	"The user to add."
//	@Success			201		{object}	apidata.User				"The user that was added."
//	@Failure			400		{object}	apidata.APIError			"Bad Request"
//	@Failure			500		{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//
//	@Router				/users [post]
func CreateUserHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		var user models.CreateUserRequest
		if err := handlertools.DecodeJSON(r, &user); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Users,
			"create_user",
		)

		createdUser, err := rs.Users.Create(r.Context(), &user)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		resp := apidata.UserTransformer(createdUser)

		w.WriteHeader(http.StatusCreated)
		if err = handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetUserHandler godoc
//
//	@Summary			Get a user.
//	@Description		Get a user.
//	@Tags				user
//	@Accept				json
//	@Produce			json
//	@Param				userId	path		string				true	"The user_id of the user to get."
//	@Success			200		{object}	apidata.User		"The user that was retrieved."
//	@Failure			404		{object}	apidata.APIError	"Not Found"
//	@Failure			500		{object}	apidata.APIError	"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/users/{userId} [get]
func GetUserHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // false positive
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		userID, err := url.PathUnescape(chi.URLParam(r, "userId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Users,
			"get_user",
		)

		user, err := rs.Users.Get(r.Context(), userID)
		if err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.UserTransformer(user)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// UpdateUserHandler godoc
//
//	@Summary			Update a user.
//	@Description		Update a user.
//	@Tags				user
//	@Accept				json
//	@Produce			json
//	@Param				userId	path		string						true	"User ID"
//	@Param				user	body		models.UpdateUserRequest	true	"Update User Request"
//	@Success			200		{object}	apidata.User				"The user that was updated."
//	@Failure			400		{object}	apidata.APIError			"Bad Request"
//	@Failure			404		{object}	apidata.APIError			"Not Found"
//	@Failure			500		{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/users/{userId} [patch]
func UpdateUserHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // not duplicate
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		userID, err := url.PathUnescape(chi.URLParam(r, "userId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		var user models.UpdateUserRequest
		if err := handlertools.DecodeJSON(r, &user); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		user.UserID = userID

		observability.I().CaptureBreadcrumb(
			observability.Category_Users,
			"update_user",
		)

		updatedUser, err := rs.Users.Update(r.Context(), &user, true)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		resp := apidata.UserTransformer(updatedUser)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// DeleteUserHandler godoc
//
//	@Summary			Delete a user
//	@Description		delete user by id
//	@Tags				user
//	@Accept				json
//	@Produce			json
//	@Param				userId	path		string					true	"User ID"
//	@Success			200		{object}	apidata.SuccessResponse	"OK"
//	@Failure			404		{object}	apidata.APIError		"Not Found"
//	@Failure			500		{object}	apidata.APIError		"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/users/{userId} [delete]
func DeleteUserHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		userID, err := url.PathUnescape(chi.URLParam(r, "userId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Users,
			"delete_user",
		)

		if err := rs.Users.Delete(r.Context(), userID); err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		handlertools.JSONOK(w, http.StatusOK)
	}
}

// ListAllUsersHandler godoc
//
//	@Summary			List all users
//	@Description		list all users
//	@Tags				user, ignore
//	@Accept				json
//	@Produce			json
//	@Param				limit	query		int					false	"Limit"
//	@Param				cursor	query		int64				false	"Cursor"
//	@Success			200		{array}		[]apidata.User		"Successfully retrieved list of users"
//	@Failure			400		{object}	apidata.APIError	"Bad Request"
//	@Failure			500		{object}	apidata.APIError	"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/users [get]
func ListAllUsersHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		limit, err := handlertools.IntFromQuery[int](r, "limit")
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		cursor, err := handlertools.IntFromQuery[int64](r, "cursor")
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Users,
			"list_all_users",
			map[string]any{
				"cursor": cursor,
				"limit":  limit,
			},
		)

		users, err := rs.Users.ListAll(r.Context(), cursor, limit)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.UserListTransformer(users)

		if err = handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// ListAllOrderedUsersHandler godoc
//
//	@Summary			List all users
//	@Description		List all users with pagination.
//	@Tags				user
//	@Accept				json
//	@Produce			json
//	@Param				pageNumber	query		int							false	"Page number for pagination, starting from 1"
//	@Param				pageSize	query		int							false	"Number of users to retrieve per page"
//	@Success			200			{object}	apidata.UserListResponse	"Successfully retrieved list of users"
//	@Failure			400			{object}	apidata.APIError			"Bad Request"
//	@Failure			500			{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/users-ordered [get]
func ListAllOrderedUsersHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // false positive
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		pageNumber, pageSize, err := handlertools.ExtractPaginationFromRequest(r)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Users,
			"list_all_ordered_users",
			map[string]any{
				"page_number": pageNumber,
				"page_size":   pageSize,
			},
		)

		users, err := rs.Users.ListAllOrdered(r.Context(), pageNumber, pageSize, "", false)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.UserListResponse{
			Users:      apidata.UserListTransformer(users.Users),
			TotalCount: users.TotalCount,
			RowCount:   users.RowCount,
		}

		if err = handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}
