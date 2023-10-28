package auth

import (
	"log"
	"net/http"

	"github.com/go-chi/jwtauth/v5"

	"github.com/getzep/zep/config"
)

const JwtAlg = "HS256"

// GenerateJWT generates a JWT token using the given config.
// Requires that ZEP_AUTH_SECRET is set in the environment.
func GenerateJWT(cfg *config.Config) string {
	secret := []byte(cfg.Auth.Secret)
	if len(secret) == 0 {
		log.Fatal("Auth secret not set. Ensure ZEP_AUTH_SECRET is set in your environment.")
	}

	tokenAuth := jwtauth.New(JwtAlg, secret, nil)
	_, tokenString, err := tokenAuth.Encode(nil)
	if err != nil {
		log.Fatal("Error generating auth token: ", err)
	}

	return tokenString
}

func JWTVerifier(cfg *config.Config) func(http.Handler) http.Handler {
	secret := []byte(cfg.Auth.Secret)
	if len(secret) == 0 {
		log.Fatal("Auth secret not set. Ensure ZEP_AUTH_SECRET is set in your environment.")
	}
	tokenAuth := jwtauth.New(JwtAlg, secret, nil)
	return jwtauth.Verifier(tokenAuth)
}
