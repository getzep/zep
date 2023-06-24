package auth

import (
	"log"

	"github.com/getzep/zep/config"

	"github.com/golang-jwt/jwt/v5"
)

func GenerateKey(cfg *config.Config) string {
	secret := []byte(cfg.Auth.Secret)
	if len(secret) == 0 {
		log.Fatal("Auth secret not set. Ensure ZEP_AUTH_SECRET is set in your environment.")
	}

	token := jwt.New(jwt.SigningMethodHS256)
	tokenString, err := token.SignedString(secret)
	if err != nil {
		log.Fatal(err)
	}

	return tokenString
}
