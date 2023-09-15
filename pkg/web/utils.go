package web

import (
	"errors"
	"net/http"

	"github.com/getzep/zep/pkg/models"
)

type ExternalPage struct {
	Title string
	URL   string
}

var ExternalPages = map[string]ExternalPage{
	"website": {
		Title: "Website",
		URL:   "https://getzep.com",
	},
	"docs": {
		Title: "Documentation",
		URL:   "https://docs.getzep.com",
	},
	"github": {
		Title: "GitHub",
		URL:   "https://github.com/getzep/zep",
	},
}

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
