package routes

import (
	"encoding/json"
	"log"
	"net/http"
	"time"
)

func HandleGetHealth(w http.ResponseWriter, r *http.Request) {
	ms := time.Now().UnixNano() / int64(time.Millisecond)

	res := HealthCheckResponse{Now: ms}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	err := json.NewEncoder(w).Encode(res)
	if err != nil {
		log.Fatalf("Error encoding health check response: %v", err)
	}
}
