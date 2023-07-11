package models

import (
	"time"

	"github.com/google/uuid"
)

//type Document interface {
//	Marker() // Marker interface
//}

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
	UUID           uuid.UUID              `json:"uuid"               bun:"type:uuid"`
	CreatedAt      time.Time              `json:"created_at"`
	Content        string                 `json:"content"`
	Metadata       map[string]interface{} `json:"metadata,omitempty"`
	CollectionUUID uuid.UUID              `json:"collection_uuid"    bun:"type:uuid"`
}

//
//func (c *DocumentCollection) CreateDocument() (Document, error) {
//	switch c.EmbeddingDimensions {
//	case 384:
//		return &Document384{}, nil
//	case 512:
//		return &Document512{}, nil
//	case 768:
//		return &Document768{}, nil
//	case 1024:
//		return &Document1024{}, nil
//	case 1536:
//		return &Document1536{}, nil
//	default:
//		return nil, fmt.Errorf("unsupported embedding dimension: %d", c.EmbeddingDimensions)
//	}
//}
//
//type DocumentBase struct {
//	UUID           uuid.UUID              `json:"uuid"               bun:"type:uuid"`
//	CreatedAt      time.Time              `json:"created_at"`
//	Content        string                 `json:"content"`
//	Metadata       map[string]interface{} `json:"metadata,omitempty"`
//	CollectionUUID uuid.UUID              `json:"collection_uuid"    bun:"type:uuid"`
//}
//
//func (d *DocumentBase) Marker() {}
//
//type Document384 struct {
//	DocumentBase
//	Embedding []float32 `json:"embedding" bun:"type:vector(384),nullzero"`
//}
//
//type Document512 struct {
//	DocumentBase
//	Embedding []float32 `json:"embedding" bun:"type:vector(512),nullzero"`
//}
//
//type Document768 struct {
//	DocumentBase
//	Embedding []float32 `json:"embedding" bun:"type:vector(768),nullzero"`
//}
//
//type Document1024 struct {
//	DocumentBase
//	Embedding []float32 `json:"embedding" bun:"type:vector(1024),nullzero"`
//}
//
//type Document1536 struct {
//	DocumentBase
//	Embedding []float32 `json:"embedding" bun:"type:vector(1536),nullzero"`
//}
