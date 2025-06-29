package handlertools

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/go-playground/validator/v10"
	"github.com/google/uuid"

	"github.com/getzep/zep/api/apidata"
	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/observability"
	"github.com/getzep/zep/lib/zerrors"
)

const (
	RequestIDHeader = "X-Zep-Request-ID"
	RequestIDKey    = "_zep_req_id"
)

var Validate = validator.New()

func AlphanumericWithUnderscores(fl validator.FieldLevel) bool {
	name := fl.Field().String()
	return regexp.MustCompile("^[a-zA-Z0-9_]+$").MatchString(name)
}

func NonEmptyStrings(fl validator.FieldLevel) bool {
	slice, ok := fl.Field().Interface().([]string)
	if !ok {
		return false
	}
	for _, s := range slice {
		if s == "" {
			return false
		}
	}
	return true
}

func RegisterValidations(validations map[string]func(fl validator.FieldLevel) bool) error {
	for name, validationFunc := range validations {
		if err := Validate.RegisterValidation(name, validationFunc); err != nil {
			logger.Error("Error registering validation", "name", name, "error", err)
		}
	}

	return nil
}

func DecodeAndValidateJSON(r *http.Request, v any) error {
	if err := DecodeJSON(r, v); err != nil {
		return err
	}
	return Validate.Struct(v)
}

func HandleErrorRequestState(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, zerrors.ErrNotFound):
		LogAndRenderError(w, fmt.Errorf("not found"), http.StatusNotFound)
	case errors.Is(err, zerrors.ErrBadRequest):
		LogAndRenderError(w, err, http.StatusBadRequest)
	case errors.Is(err, zerrors.ErrUnauthorized):
		LogAndRenderError(w, err, http.StatusUnauthorized)
	case errors.Is(err, zerrors.ErrDeprecated):
		LogAndRenderError(w, err, http.StatusGone)
	case errors.Is(err, zerrors.ErrLockAcquisitionFailed):
		LogAndRenderError(w, err, http.StatusTooManyRequests)
	case errors.Is(err, zerrors.ErrSessionEnded):
		LogAndRenderError(w, err, http.StatusConflict)
	default:
		LogAndRenderError(w, err, http.StatusInternalServerError)
	}
}

type requestStateOptions struct {
	noCache bool
	// Indicates whether the handler is public (i.e. uses token with zmiddleware.PublicKeyAuthorizationPrefix)
	publicHandler bool
}

type RequestStateOption interface {
	apply(*requestStateOptions)
}

type noCacheRequestStateOption bool

func (r noCacheRequestStateOption) apply(opts *requestStateOptions) {
	opts.noCache = bool(r)
}

func WithoutFlagCache(c bool) RequestStateOption {
	return noCacheRequestStateOption(c)
}

type publicHandlerRequestStateOption bool

func (r publicHandlerRequestStateOption) apply(opts *requestStateOptions) {
	opts.publicHandler = bool(r)
}

func PublicHandler(c bool) RequestStateOption {
	return publicHandlerRequestStateOption(c)
}

// IntFromQuery extracts a query string value and converts it to an int
// if it is not empty. If the value is empty, it returns 0.
func IntFromQuery[T ~int | ~int32 | int64](
	r *http.Request,
	param string,
) (T, error) {
	bitsize := 0

	p := strings.TrimSpace(r.URL.Query().Get(param))
	var pInt T
	if p != "" {
		switch any(pInt).(type) {
		case int:
		case int32:
			bitsize = 32 //nolint:revive // 32 is the size of an int32
		case int64:
			bitsize = 64 //nolint:revive // 64 is the size of an int64
		default:
			return 0, errors.New("unsupported type")
		}

		pInt, err := strconv.ParseInt(p, 10, bitsize) //nolint:revive // 10 is the base
		if err != nil {
			return 0, err
		}
		return T(pInt), nil
	}
	return 0, nil
}

func FloatFromQuery[T ~float32 | ~float64](r *http.Request, param string) (T, error) {
	p := strings.TrimSpace(r.URL.Query().Get(param))
	if p == "" {
		return 0, nil
	}

	var ft T
	var bitsize int
	switch any(ft).(type) {
	case float32:
		bitsize = 32 //nolint:revive // 32 is the size of a float32
	case float64:
		bitsize = 64 //nolint:revive // 64 is the size of a float64
	default:
		return 0, errors.New("unsupported type")
	}

	pf, err := strconv.ParseFloat(p, bitsize)
	if err != nil {
		return 0, err
	}
	return T(pf), nil
}

// BoolFromQuery extracts a query string value and converts it to a bool
func BoolFromQuery(r *http.Request, param string) (bool, error) {
	p := strings.TrimSpace(r.URL.Query().Get(param))
	if p != "" {
		return strconv.ParseBool(p)
	}
	return false, nil
}

// BoundedStringFromQuery extracts a query string value and checks if it is one of the provided options.
func BoundedStringFromQuery(r *http.Request, param string, options []string) (string, error) {
	p := strings.TrimSpace(r.URL.Query().Get(param))
	if p == "" {
		return "", nil
	}
	for _, option := range options {
		if p == option {
			return p, nil
		}
	}
	return "", fmt.Errorf("invalid value for %s", param)
}

// EncodeJSON encodes data into JSON and writes it to the response writer.
func EncodeJSON(w http.ResponseWriter, data any) error {
	return json.NewEncoder(w).Encode(data)
}

// DecodeJSON decodes a JSON request body into the provided data struct.
func DecodeJSON(r *http.Request, data any) error {
	return json.NewDecoder(r.Body).Decode(&data)
}

func JSONError(w http.ResponseWriter, e error, code int) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(code)
	errorResponse := zerrors.ErrorResponse{
		Message: e.Error(),
	}
	if err := EncodeJSON(w, errorResponse); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
}

func JSONOK(w http.ResponseWriter, code int) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(code)
	r := apidata.SuccessResponse{
		Message: "OK",
	}
	if err := EncodeJSON(w, r); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
}

// LogAndRenderError logs, sanitizes, and renders an error response.
func LogAndRenderError(w http.ResponseWriter, err error, status int) {
	// log errors from 500 onwards (inclusive)
	if status >= http.StatusInternalServerError {
		if errors.Is(err, zerrors.ErrInternalCustomMessage) {
			var customMsgInternalErr *zerrors.CustomMessageInternalError
			if errors.As(err, &customMsgInternalErr) {
				observability.I().CaptureError("Custom message internal error", errors.New(customMsgInternalErr.InternalMessage))
			}
		} else {
			observability.I().CaptureError("Internal server error", err)
		}
	}

	// Add descriptive error messages for request body too large
	if err.Error() == "http: request body too large" {
		status = http.StatusRequestEntityTooLarge
		err = fmt.Errorf(
			"request body too large",
		)
	}

	// sanitize error if it is an auth error
	if status == http.StatusUnauthorized {
		err = zerrors.ErrUnauthorized
	}

	// If the error is a bad request, return a 400
	if strings.Contains(err.Error(), "is deleted") || errors.Is(err, zerrors.ErrBadRequest) {
		status = http.StatusBadRequest
	}

	// Handle too many requests error
	if status == http.StatusTooManyRequests {
		err = errors.New("too many concurrent writes to the same record")
	}

	JSONError(w, err, status)
}

// UUIDFromURL parses a UUID from a Path parameter. If the UUID is invalid, an error is
// rendered and uuid.Nil is returned.
func UUIDFromURL(r *http.Request, w http.ResponseWriter, paramName string) uuid.UUID {
	value := chi.URLParam(r, paramName)

	objUUID, err := uuid.Parse(value)
	if err != nil {
		LogAndRenderError(
			w,
			fmt.Errorf("unable to parse UUID: %w", err),
			http.StatusBadRequest,
		)
		return uuid.Nil
	}

	return objUUID
}

func ExtractPaginationFromRequest(r *http.Request) (pNum, pSize int, pErr error) {
	pageNumber, err := IntFromQuery[int](r, "pageNumber")
	if err != nil {
		return 0, 0, err
	}

	pageSize, err := IntFromQuery[int](r, "pageSize")
	if err != nil {
		return 0, 0, err
	}

	return pageNumber, pageSize, nil
}
