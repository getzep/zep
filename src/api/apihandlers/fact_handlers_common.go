package apihandlers

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/getzep/zep/api/apidata"
	"github.com/getzep/zep/api/handlertools"
	"github.com/getzep/zep/lib/observability"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

// GetFactHandler godoc
//
//	@Summary			Returns a fact by UUID
//	@Description		get fact by uuid
//	@Tags				fact
//	@Accept				json
//	@Produce			json
//	@Param				factUUID	path		string					true	"Fact UUID"
//	@Success			200			{object}	apidata.FactResponse	"The fact with the specified UUID."
//	@Failure			404			{object}	apidata.APIError		"Not Found"
//	@Failure			500			{object}	apidata.APIError		"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/facts/{factUUID} [get]
func GetFactHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // not duplicate
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		factUUIDValue, err := url.PathUnescape(chi.URLParam(r, "factUUID"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Facts,
			"get_fact",
		)

		factUUID, err := uuid.Parse(factUUIDValue)
		if err != nil {
			handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
			return
		}

		fact, err := getFact(r.Context(), factUUID, rs)
		if err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		resp := apidata.FactResponse{
			Fact: apidata.FactTransformer(*fact),
		}

		if err := handlertools.EncodeJSON(w, resp); err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// DeleteFactHandler godoc
//
//	@Summary			Delete a fact for the given UUID
//	@Description		delete a fact
//	@Tags				fact
//	@Accept				json
//	@Produce			json
//	@Param				factUUID	path		string					true	"Fact UUID"
//	@Success			201			{string}	apidata.SuccessResponse	"Deleted"
//	@Failure			404			{object}	apidata.APIError		"Not Found"
//	@Failure			500			{object}	apidata.APIError		"Internal Server Error"
//	@Security			Bearer
//	@x-fern-audiences	["cloud", "community"]
//	@Router				/facts/{factUUID} [delete]
func DeleteFactHandler(as *models.AppState) http.HandlerFunc { // nolint:dupl // not duplicate
	return func(w http.ResponseWriter, r *http.Request) {
		rs, err := handlertools.NewRequestState(r, as)
		if err != nil {
			handlertools.HandleErrorRequestState(w, err)
			return
		}

		factUUIDValue, err := url.PathUnescape(chi.URLParam(r, "factUUID"))
		if err != nil {
			handlertools.LogAndRenderError(w, err, http.StatusBadRequest)
			return
		}

		observability.I().CaptureBreadcrumb(
			observability.Category_Facts,
			"delete_fact",
		)

		factUUID, err := uuid.Parse(factUUIDValue)
		if err != nil {
			handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
			return
		}

		err = deleteSessionFact(r.Context(), factUUID, rs)
		if err != nil {
			if errors.Is(err, zerrors.ErrNotFound) {
				handlertools.LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
				return
			}

			handlertools.LogAndRenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
	}
}
