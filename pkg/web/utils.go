package web

import (
	"errors"
	"net/http"

	"github.com/getzep/zep/pkg/models"
)

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
