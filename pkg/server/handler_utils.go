package server

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// APIError represents an error response. Used for swagger documentation.
type APIError struct {
	Message string `json:"message"`
}

// extractQueryStringValueToInt extracts a query string value and converts it to an int
// if it is not empty. If the value is empty, it returns 0.
func extractQueryStringValueToInt[T ~int | int32 | int64](
	r *http.Request,
	param string,
) (T, error) {
	p := r.URL.Query().Get(param)
	var pInt T
	if p != "" {
		switch any(pInt).(type) {
		case int:
			pInt, err := strconv.ParseInt(p, 10, 32)
			if err != nil {
				return 0, err
			}
			return T(pInt), nil
		case int32:
			pInt, err := strconv.ParseInt(p, 10, 32)
			if err != nil {
				return 0, err
			}
			return T(pInt), nil
		case int64:
			pInt, err := strconv.ParseInt(p, 10, 64)
			if err != nil {
				return 0, err
			}
			return T(pInt), nil
		default:
			return 0, errors.New("unsupported type")
		}
	}
	return 0, nil
}

// encodeJSON encodes data into JSON and writes it to the response writer.
func encodeJSON(w http.ResponseWriter, data interface{}) error {
	return json.NewEncoder(w).Encode(data)
}

// decodeJSON decodes a JSON request body into the provided data struct.
func decodeJSON(r *http.Request, data interface{}) error {
	return json.NewDecoder(r.Body).Decode(&data)
}

// renderError renders an error response.
func renderError(w http.ResponseWriter, err error, status int) {
	if status != http.StatusNotFound {
		// Don't log not found errors
		log.Error(err)
	}
	if strings.Contains(err.Error(), "is deleted") {
		status = http.StatusBadRequest
	}
	http.Error(w, err.Error(), status)
}

// parseUUIDFromURL parses a UUID from a URL parameter. If the UUID is invalid, an error is
// rendered and uuid.Nil is returned.
func parseUUIDFromURL(r *http.Request, w http.ResponseWriter, paramName string) uuid.UUID {
	uuidStr := chi.URLParam(r, paramName)
	documentUUID, err := uuid.Parse(uuidStr)
	if err != nil {
		renderError(
			w,
			fmt.Errorf("unable to parse document UUID: %w", err),
			http.StatusBadRequest,
		)
		return uuid.Nil
	}
	return documentUUID
}
