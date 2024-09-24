package models

import (
	"time"

	"github.com/google/uuid"
)

type Fact struct {
	UUID      uuid.UUID `json:"uuid"`
	CreatedAt time.Time `json:"created_at"`
	Fact      string    `json:"fact"`
	Rating    *float64  `json:"rating,omitempty"`
}
