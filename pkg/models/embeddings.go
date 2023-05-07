package models

import "github.com/google/uuid"

type EmbeddingsConfig struct {
	Model      string
	Dimensions int64
	Enabled    bool
}

type Embeddings struct {
	TextUUID  uuid.UUID // MemoryStore's unique ID associated with this text.
	Text      string
	Embedding []float32
}
