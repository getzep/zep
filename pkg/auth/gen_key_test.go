package auth

import (
	"testing"

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

	token := GenerateKey(cfg)

	// Validate the generated token
	claims := jwt.MapClaims{}
	parsedToken, err := jwt.ParseWithClaims(token, claims, func(*jwt.Token) (interface{}, error) {
		return []byte(cfg.Auth.Secret), nil
	})

	if assert.NoError(t, err) {
		assert.True(t, parsedToken.Valid)
	}
}
