package models

import "github.com/google/uuid"

type EmbeddingModel struct {
	Service      string `json:"service"`
	Dimensions   int    `json:"dimensions"`
	IsNormalized bool   `json:"normalized"`
}

type TextData struct {
	TextUUID  uuid.UUID `json:"uuid,omitempty"` // MemoryStore's unique ID associated with this text.
	Text      string    `json:"text"`
	Embedding []float32 `json:"embedding,omitempty"`
	Language  string    `json:"language"`
}

type TextEmbeddingCollection struct {
	UUID       uuid.UUID  `json:"uuid,omitempty"`
	Name       string     `json:"name,omitempty"`
	Embeddings []TextData `json:"documents"`
}
