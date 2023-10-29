package models

import (
	"time"

	"github.com/google/uuid"
)

type Message struct {
	UUID       uuid.UUID              `json:"uuid"`
	CreatedAt  time.Time              `json:"created_at"`
	UpdatedAt  time.Time              `json:"updated_at"`
	Role       string                 `json:"role"`
	Content    string                 `json:"content"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
	TokenCount int                    `json:"token_count"`
}

type MessageListResponse struct {
	Messages   []Message `json:"messages"`
	TotalCount int       `json:"total_count"`
	RowCount   int       `json:"row_count"`
}

type SummaryListResponse struct {
	Summaries  []Summary `json:"summaries"`
	TotalCount int       `json:"total_count"`
	RowCount   int       `json:"row_count"`
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
