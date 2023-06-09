package models

import "github.com/google/uuid"

type EmbeddingModel struct {
	Name         string `json:"name"`
	Dimensions   int    `json:"dimensions"`
	IsNormalized bool   `json:"normalized"`
}

type DocumentEmbeddings struct {
	TextUUID  uuid.UUID `json:"uuid,omitempty"` // MemoryStore's unique ID associated with this text.
	Text      string    `json:"text"`
	Embedding []float32 `json:"embedding,omitempty"`
	Language  string    `json:"language"`
}

type DocumentCollection struct {
	UUID      uuid.UUID            `json:"uuid,omitempty"`
	Name      string               `json:"name,omitempty"`
	Documents []DocumentEmbeddings `json:"documents"`
}
