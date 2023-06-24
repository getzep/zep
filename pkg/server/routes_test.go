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
