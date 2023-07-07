package models

import (
	"time"

	"github.com/google/uuid"
)

type Message struct {
	UUID       uuid.UUID              `json:"uuid"`
	CreatedAt  time.Time              `json:"created_at"`
	Role       string                 `json:"role"`
	Content    string                 `json:"content"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
	TokenCount int                    `json:"token_count"`
}

type Summary struct {
	UUID             uuid.UUID              `json:"uuid"`
	CreatedAt        time.Time              `json:"created_at"`
	Content          string                 `json:"content"`
	SummaryPointUUID uuid.UUID              `json:"recent_message_uuid"` // The most recent message UUID that was used to generate this summary
	Metadata         map[string]interface{} `json:"metadata,omitempty"`
	TokenCount       int                    `json:"token_count"`
}

type Memory struct {
	Messages []Message              `json:"messages"`
	Summary  *Summary               `json:"summary,omitempty"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

type MessageEvent struct {
	SessionID string                 `json:"sessionId"`
	Messages  []Message              `json:"messages"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
}
