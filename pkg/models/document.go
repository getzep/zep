package models

import (
	"time"

	"github.com/google/uuid"
)

type DocumentCollection struct {
	UUID                uuid.UUID              `json:"uuid"`
	CreatedAt           time.Time              `json:"created_at"`
	Name                string                 `json:"name"`
	Description         string                 `json:"description"`
	Metadata            map[string]interface{} `json:"metadata,omitempty"`
	TableName           string                 `json:"table_name"`
	EmbeddingDimensions int                    `json:"embedding_dimensions"`
	DistanceFunction    string                 `json:"distance_function"` // Distance function to use for index
	IsNormalized        bool                   `json:"is_normalized"`     // Are the embeddings normalized?
	IsIndexed           bool                   `json:"is_indexed"`        // Has an index been created on the collection table?
}

type Document struct {
	UUID           uuid.UUID              `json:"uuid"`
	CreatedAt      time.Time              `json:"created_at"`
	Content        string                 `json:"content"`
	Metadata       map[string]interface{} `json:"metadata,omitempty"`
	CollectionUUID uuid.UUID              `json:"collection_uuid"`
	Embedding      []float32              `json:"embedding"`
}
