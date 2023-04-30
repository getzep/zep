package routes

import (
	"encoding/json"
	"log"
	"net/http"
	"time"
)

func HandleGetHealth(httpWriter http.ResponseWriter, _ *http.Request) {
	ms := time.Now().UnixNano() / int64(time.Millisecond)

	res := HealthCheckResponse{Now: ms}

	httpWriter.Header().Set("Content-Type", "application/json")
	httpWriter.WriteHeader(http.StatusOK)
	err := json.NewEncoder(httpWriter).Encode(res)
	if err != nil {
		log.Fatalf("Error encoding health check response: %v", err)
	}
}
