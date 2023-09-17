package webhandlers

import (
	"errors"
	"net/http"

	"github.com/getzep/zep/internal"

	"github.com/getzep/zep/pkg/models"
)

var log = internal.GetLogger()

func handleError(w http.ResponseWriter, err error, message string) {
	switch {
	case errors.Is(err, models.ErrNotFound):
		http.Error(w, message, http.StatusNotFound)
	case errors.Is(err, models.ErrBadRequest):
		http.Error(w, message, http.StatusBadRequest)
	default:
		http.Error(w, message, http.StatusInternalServerError)
	}
	log.Errorf("%s: %s", message, err)
}
