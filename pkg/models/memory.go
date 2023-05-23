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

// MessageMetadata is used internally to marshal metadata updates for a given message.
// Key is the metadata key to update. If the key doesn't exist, it will be created.
// If the key exists, the value will be updated.
// Metadata is a map of key/value pairs to create or update at the given Key.
// An empty Metadata map will delete the given Key.
type MessageMetadata struct {
	UUID     uuid.UUID              `json:"uuid"`
	Key      string                 `json:"key"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
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
