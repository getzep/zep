package handlertools

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/stretchr/testify/assert"
)

func TestExtractQueryStringValueToInt(t *testing.T) {
	req := httptest.NewRequest("GET", "/?param=123", nil)
	got, err := IntFromQuery[int](req, "param")
	assert.NoError(t, err, "extractQueryStringValueToInt() error = %v", err)
	assert.Equal(t, 123, got, "extractQueryStringValueToInt() = %v, want %v", got, 123)
}

func TestParseUUIDFromURL(t *testing.T) {
	r := chi.NewRouter()
	r.Get("/{uuid}", func(w http.ResponseWriter, r *http.Request) {
		urlUUID := UUIDFromURL(r, w, "uuid")
		assert.NotNil(t, urlUUID)
	})

	ts := httptest.NewServer(r)
	defer ts.Close()

	// Test with valid UUID
	validUUID := uuid.New()
	res, err := http.Get(ts.URL + "/" + validUUID.String())
	assert.NoError(t, err)
	assert.Equal(t, http.StatusOK, res.StatusCode)

	// Test with invalid UUID
	res, err = http.Get(ts.URL + "/invalid_uuid")
	assert.NoError(t, err)
	assert.Equal(t, http.StatusBadRequest, res.StatusCode)
}
