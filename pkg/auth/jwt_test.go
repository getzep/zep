package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/jwtauth/v5"
	"github.com/stretchr/testify/require"

	"github.com/getzep/zep/config"

	"github.com/golang-jwt/jwt/v5"

	"github.com/stretchr/testify/assert"
)

func TestGenerateKey(t *testing.T) {
	// Set up test app state with a sample secret
	cfg := &config.Config{
		Auth: config.AuthConfig{
			Secret: "test-secret",
		},
	}

	token := GenerateJWT(cfg)

	// Validate the generated token
	claims := jwt.MapClaims{}
	parsedToken, err := jwt.ParseWithClaims(token, claims, func(*jwt.Token) (interface{}, error) {
		return []byte(cfg.Auth.Secret), nil
	})

	if assert.NoError(t, err) {
		assert.True(t, parsedToken.Valid)
	}
}

func TestJWTVerifier(t *testing.T) {
	cfg := &config.Config{
		Auth: config.AuthConfig{
			Secret: "test-secret",
		},
	}
	router := chi.NewRouter()
	testHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	router.Use(JWTVerifier(cfg))
	router.Use(jwtauth.Authenticator)
	router.Handle("/", testHandler)

	t.Run("valid JWT token", func(t *testing.T) {
		tokenAuth := jwtauth.New(JwtAlg, []byte(cfg.Auth.Secret), nil)
		_, tokenString, _ := tokenAuth.Encode(nil)
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.Header.Set("Authorization", "Bearer "+tokenString)
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)

		require.Equal(t, http.StatusOK, res.Code)
	})

	t.Run("missing JWT token", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)

		require.Equal(t, http.StatusUnauthorized, res.Code)
	})

	t.Run("invalid JWT token", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.Header.Set("Authorization", "Bearer invalid-token")
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)

		require.Equal(t, http.StatusUnauthorized, res.Code)
	})
}
