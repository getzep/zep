package handlertools

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

var log = internal.GetLogger()

// IntFromQuery extracts a query string value and converts it to an int
// if it is not empty. If the value is empty, it returns 0.
func IntFromQuery[T ~int | int32 | int64](
	r *http.Request,
	param string,
) (T, error) {
	bitsize := 0

	p := r.URL.Query().Get(param)
	var pInt T
	if p != "" {
		switch any(pInt).(type) {
		case int:
		case int32:
			bitsize = 32
		case int64:
			bitsize = 64
		default:
			return 0, errors.New("unsupported type")
		}

		pInt, err := strconv.ParseInt(p, 10, bitsize)
		if err != nil {
			return 0, err
		}
		return T(pInt), nil
	}
	return 0, nil
}

// BoolFromQuery extracts a query string value and converts it to a bool
func BoolFromQuery(r *http.Request, param string) (bool, error) {
	p := r.URL.Query().Get(param)
	if p != "" {
		return strconv.ParseBool(p)
	}
	return false, nil
}

// EncodeJSON encodes data into JSON and writes it to the response writer.
func EncodeJSON(w http.ResponseWriter, data interface{}) error {
	return json.NewEncoder(w).Encode(data)
}

// DecodeJSON decodes a JSON request body into the provided data struct.
func DecodeJSON(r *http.Request, data interface{}) error {
	return json.NewDecoder(r.Body).Decode(&data)
}

// RenderError renders an error response.
func RenderError(w http.ResponseWriter, err error, status int) {
	if err.Error() == "http: request body too large" {
		status = http.StatusRequestEntityTooLarge
		err = fmt.Errorf(
			"request body too large. if you're uploading documents, reduce the batch size or size of the document chunks",
		)
	}

	if status != http.StatusNotFound {
		// Don't log not found errors
		log.Error(err)
	}

	if strings.Contains(err.Error(), "is deleted") || errors.Is(err, models.ErrBadRequest) {
		status = http.StatusBadRequest
	}

	http.Error(w, err.Error(), status)
}

// UUIDFromURL parses a UUID from a Path parameter. If the UUID is invalid, an error is
// rendered and uuid.Nil is returned.
func UUIDFromURL(r *http.Request, w http.ResponseWriter, paramName string) uuid.UUID {
	uuidStr := chi.URLParam(r, paramName)
	documentUUID, err := uuid.Parse(uuidStr)
	if err != nil {
		RenderError(
			w,
			fmt.Errorf("unable to parse document UUID: %w", err),
			http.StatusBadRequest,
		)
		return uuid.Nil
	}
	return documentUUID
}
