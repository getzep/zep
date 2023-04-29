package main

import (
	"encoding/json"
	"net/http"
	"time"
)

func handleGetHealth(w http.ResponseWriter, r *http.Request) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ms := time.Now().UnixNano() / int64(time.Millisecond)

		res := HealthCheckResponse{Now: ms}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(res)
	}
}
