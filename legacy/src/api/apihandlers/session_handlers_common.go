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
	"github.com/getzep/zep/lib/util"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

// CreateSessionHandler godoc
//
//	@Summary			Add a session
//	@Description		Create New Session
//	@Tags				session
//	@Accept				json
//	@Produce			json
//	@Param				session	body		models.CreateSessionRequest	true	"Session"
//	@Success			201		{object}	apidata.Session				"The added session."
//	@Failure			400		{object}	apidata.APIError			"Bad Request"
//	@failure			500		{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions [post]
func CreateSessionHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // not duplicate
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		var session models.CreateSessionRequest
		if err = handlertools.DecodeJSON(r, &session); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"create_session",
		)

		if util.SafelyDereference(session.UserID) != "" {
			_, err := rs.Users.Get(r.Context(), *session.UserID)
			if err != nil {
				if errors.Is(err, zerrors.ErrNotFound) {
					handlertools.LogAndRenderError(w, fmt.Errorf("user not found"), http.StatusBadRequest)
					return
				}

				handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
				return
			}
		}

		newSession, err := rs.Memories.CreateSession(r.Context(), &session)
		if err != nil {
			if errors.Is(err, zerrors.ErrBadRequest) {
				handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.SessionTransformer(newSession)

		w.WriteHeader(http.StatusCreated)

		if err = handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// UpdateSessionHandler godoc
//
//	@Summary			Update a session
//	@Description		Update Session Metadata
//	@Tags				session
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string						true	"Session ID"
//	@Param				session		body		models.UpdateSessionRequest	true	"Session"
//	@Success			200			{object}	apidata.Session				"The updated session."
//	@Failure			400			{object}	apidata.APIError			"Bad Request"
//	@Failure			404			{object}	apidata.APIError			"Not Found"
//	@Failure			409			{object}	apidata.APIError			"Conflict"
//	@failure			500			{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions/{sessionId} [patch]
func UpdateSessionHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // not duplicate
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		sessionID, err := url.PathUnescape(chi.URLParam(r, "sessionId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		var session models.UpdateSessionRequest
		if err := handlertools.DecodeJSON(r, &session); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}
		session.SessionID = sessionID

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"update_session",
		)

		updatedSession, err := rs.Memories.UpdateSession(r.Context(), &session, false)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		resp := apidata.SessionTransformer(updatedSession)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetSessionHandler godoc
//
//	@Summary			Returns a session by ID
//	@Description		get session by id
//	@Tags				session
//	@Accept				json
//	@Produce			json
//	@Param				sessionId	path		string				true	"Session ID"
//	@Success			200			{object}	apidata.Session		"The session with the specified ID."
//	@Failure			404			{object}	apidata.APIError	"Not Found"
//	@Failure			500			{object}	apidata.APIError	"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions/{sessionId} [get]
func GetSessionHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // not duplicate
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		sessionID, err := url.PathUnescape(chi.URLParam(r, "sessionId"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"get_session",
		)

		session, err := rs.Memories.GetSession(r.Context(), sessionID)
		if err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.SessionTransformer(session)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetSessionListHandler godoc
//
//	@Summary			Returns all sessions
//	@Description		get all sessions with optional limit and cursor for pagination
//	@Tags				session, ignore
//	@Accept				json
//	@Produce			json
//	@Param				limit	query		integer	false	"Limit the number of results returned"
//	@Param				cursor	query		int64	false	"Cursor for pagination"
//	@Success			200		{array}		[]apidata.Session
//	@Failure			400		{object}	apidata.APIError	"Bad Request"
//	@Failure			500		{object}	apidata.APIError	"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions [get]
func GetSessionListHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		var limit int
		if limit, err = handlertools.IntFromQuery[int](r, "limit"); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		var cursor int64
		if cursor, err = handlertools.IntFromQuery[int64](r, "cursor"); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"get_session_list",
			map[string]any{
				"limit":  limit,
				"cursor": cursor,
			},
		)

		sessions, err := rs.Memories.ListSessions(r.Context(), cursor, limit)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.SessionListTransformer(sessions)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetOrderedSessionListHandler godoc
//
//	@Summary			Returns all sessions in a specified order
//	@Description		Get all sessions with optional page number, page size, order by field and order direction for pagination.
//	@Tags				session
//	@Accept				json
//	@Produce			json
//	@Param				page_number	query		integer						false	"Page number for pagination, starting from 1"
//	@Param				page_size	query		integer						false	"Number of sessions to retrieve per page"
//	@Param				order_by	query		string						false	"Field to order the results by: created_at, updated_at, user_id, session_id"
//	@Param				asc			query		boolean						false	"Order direction: true for ascending, false for descending"
//	@Success			200			{object}	apidata.SessionListResponse	"List of sessions"
//	@Failure			400			{object}	apidata.APIError			"Bad Request"
//	@Failure			500			{object}	apidata.APIError			"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/sessions-ordered [get]
func GetOrderedSessionListHandler(as *models.AppState) http.HandlerFunc { //nolint:dupl // not duplicate
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

		orderedBy, err := handlertools.BoundedStringFromQuery(
			r,
			"order_by",
			[]string{"created_at", "updated_at", "user_id", "session_id"},
		)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		asc, err := handlertools.BoolFromQuery(r, "asc")
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"list_sessions_ordered",
			map[string]any{
				"page_number": pageNumber,
				"page_size":   pageSize,
			},
		)

		sessions, err := rs.Memories.ListSessionsOrdered(
			r.Context(),
			pageNumber,
			pageSize,
			orderedBy,
			asc,
		)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.SessionListResponse{
			Sessions:   apidata.SessionListTransformer(sessions.Sessions),
			TotalCount: sessions.TotalCount,
			RowCount:   sessions.RowCount,
		}

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// SearchSessionsHandler godoc
//
//	@Summary			Search sessions for the specified query.
//	@Description		Search sessions for the specified query.
//	@Tags				search
//	@Accept				json
//	@Produce			json
//	@Param				limit	query		integer							false	"The maximum number of search results to return. Defaults to None (no limit)."
//	@Param				request	body		models.SessionSearchQuery		true	"A SessionSearchQuery object representing the search query."
//	@Success			200		{object}	apidata.SessionSearchResponse	"A SessionSearchResponse object representing the search results."
//	@Failure			500		{object}	apidata.APIError				"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//
//	@Router				/sessions/search [post]
func SearchSessionsHandler(as *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		var request models.SessionSearchQuery
		if err := handlertools.DecodeJSON(r, &request); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		limit, err := handlertools.IntFromQuery[int](r, "limit")
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		if limit < 0 {
			handlertools.LogAndRenderError(w, fmt.Errorf("limit cannot be negative"), http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Sessions,
			"search_memory",
			request.BreadcrumbFields(),
		)

		searchResult, err := rs.Memories.SearchSessions(
			r.Context(),
			&request,
			limit,
		)
		if err != nil {
			if errors.Is(err, zerrors.ErrBadRequest) {
				handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.SessionSearchResponse{
			Results: apidata.SessionSearchResultListTransformer(searchResult.Results),
		}

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// ListUserSessionsHandler godoc
//
//	@Summary			List all sessions for a user
//	@Description		list all sessions for a user by user id
//	@Tags				user
//	@Accept				json
//	@Produce			json
//	@Param				userId	path		string	true	"User ID"
//	@Success			200		{array}		[]apidata.Session
//	@Failure			500		{object}	apidata.APIError	"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/users/{userId}/sessions [get]
func ListUserSessionsHandler(as *models.AppState) http.HandlerFunc {
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
			"list_user_sessions",
		)

		sessions, err := rs.Users.GetSessionsForUser(r.Context(), userID)
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.SessionListTransformer(sessions)

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}
