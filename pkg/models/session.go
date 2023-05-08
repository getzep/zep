package models

import (
	"time"

	"github.com/google/uuid"
)

type Session struct {
	UUID      uuid.UUID              `json:"uuid"`
	CreatedAt time.Time              `json:"created_at"`
	SessionID string                 `json:"session_id"`
	Metadata  map[string]interface{} `json:"meta"`
}
