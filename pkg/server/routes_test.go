package server

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/require"
)

func TestAuthMiddleware(t *testing.T) {
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	t.Run("auth required", func(t *testing.T) {
		appState := &models.AppState{
			Config: &config.Config{
				Auth: config.AuthConfig{
					Secret:   "test-secret",
					Required: true,
				},
			},
		}

		router := setupRouter(appState)
		router.Handle("/", testHandler)

		req := httptest.NewRequest(http.MethodGet, "/", nil)
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)
		require.Equal(t, http.StatusUnauthorized, res.Code)
	})

	t.Run("auth not required", func(t *testing.T) {
		appState := &models.AppState{
			Config: &config.Config{
				Auth: config.AuthConfig{
					Secret:   "test-secret",
					Required: false,
				},
			},
		}

		router := setupRouter(appState)
		router.Handle("/", testHandler)

		req := httptest.NewRequest(http.MethodGet, "/", nil)
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)
		require.Equal(t, http.StatusOK, res.Code)
	})
}

func TestSendVersion(t *testing.T) {
	nextHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {})

	handler := SendVersion(nextHandler)

	req, err := http.NewRequest("GET", "/", nil)
	if err != nil {
		t.Fatal(err)
	}

	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Header().Get(versionHeader) != config.VersionString {
		t.Errorf("handler returned wrong version header: got %v want %v",
			rr.Header().Get(versionHeader), config.VersionString)
	}
}
