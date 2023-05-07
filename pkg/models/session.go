package models

import (
	"github.com/google/uuid"
	"time"
)

type Session struct {
	UUID      uuid.UUID              `json:"uuid"`
	CreatedAt time.Time              `json:"created_at"`
	SessionID string                 `json:"session_id"`
	Metadata  map[string]interface{} `json:"meta"`
}
