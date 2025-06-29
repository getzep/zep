
package middleware

import (
	"context"
	"net/http"
	"strings"

	"github.com/getzep/zep/lib/config"
)

const secretKeyRequestTokenType = "secret-key"

func SecretKeyAuthMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		parts := strings.Split(authHeader, " ")
		if len(parts) != 2 {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}

		prefix, tokenString := parts[0], parts[1]
		if prefix != apiKeyAuthorizationPrefix {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}

		if tokenString != config.ApiSecret() {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}

		ctx := r.Context()
		ctx = context.WithValue(ctx, RequestTokenType, secretKeyRequestTokenType)
		ctx = context.WithValue(ctx, ProjectId, config.ProjectUUID())

		r = r.WithContext(ctx)

		next.ServeHTTP(w, r)
	})
}
