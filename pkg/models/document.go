package models

import (
	"time"

	"github.com/google/uuid"
)

/* Collection  Models */

type DocumentCollection struct {
	UUID                uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt           time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt           time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	Name                string                 `bun:",notnull,unique"`
	Description         string                 `bun:",notnull"`
	Metadata            map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
	TableName           string                 `bun:",notnull"`
	EmbeddingModelName  string                 `bun:",notnull"`
	EmbeddingDimensions int                    `bun:",notnull"`
	IsAutoEmbedded      bool                   `bun:",notnull"` // Is the collection automatically embedded by Zep?
	DistanceFunction    string                 `bun:",notnull"` // Distance function to use for index
	IsNormalized        bool                   `bun:",notnull"` // Are the embeddings normalized?
	IsIndexed           bool                   `bun:",notnull"` // Has an index been created on the collection table?
	ListCount           int                    `bun:",notnull"` // Number of lists in the collection index
	ProbeCount          int                    `bun:",notnull"` // Number of probes to use when searching the index
	*DocumentCollectionCounts
}

type DocumentCollectionCounts struct {
	DocumentCount         int `bun:"document_count"          json:"document_count"`          // Number of documents in the collection
	DocumentEmbeddedCount int `bun:"document_embedded_count" json:"document_embedded_count"` // Number of documents with embeddings
}

type CreateDocumentCollectionRequest struct {
	Name                string                 `json:"name"                 validate:"required,alphanum,min=3,max=40"`
	Description         string                 `json:"description"          validate:"omitempty,max=1000"`
	Metadata            map[string]interface{} `json:"metadata,omitempty"`
	EmbeddingDimensions int                    `json:"embedding_dimensions" validate:"required,numeric,min=8,max=2000"`
	// these needs to be pointers so that we can distinguish between false and unset when validating
	IsAutoEmbedded *bool `json:"is_auto_embedded"     validate:"required,boolean"`
}

type UpdateDocumentCollectionRequest struct {
	Description string                 `json:"description"        validate:"max=1000"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

type DocumentCollectionResponse struct {
	UUID                uuid.UUID              `json:"uuid"`
	CreatedAt           time.Time              `json:"created_at"`
	UpdatedAt           time.Time              `json:"updated_at"`
	Name                string                 `json:"name"`
	Description         string                 `json:"description"`
	Metadata            map[string]interface{} `json:"metadata,omitempty"`
	EmbeddingModelName  string                 `json:"embedding_model_name,omitempty"`
	EmbeddingDimensions int                    `json:"embedding_dimensions"`
	IsAutoEmbedded      bool                   `json:"is_auto_embedded"`
	IsNormalized        bool                   `json:"is_normalized"`
	IsIndexed           bool                   `json:"is_indexed"`
	*DocumentCollectionCounts
}

/* Document Models */

type DocumentBase struct {
	UUID       uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt  time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt  time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt  time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	DocumentID string                 `bun:",unique,nullzero"`
	Content    string                 `bun:",nullzero"`
	Metadata   map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
	IsEmbedded bool                   `bun:",nullzero"`
}

type Document struct {
	DocumentBase
	Embedding []float32 `bun:"type:vector,nullzero" json:"embedding,omitempty"`
}

type SearchDocumentQuery struct {
	*Document
	Score float64 `json:"score" bun:"score"`
}

type CreateDocumentRequest struct {
	DocumentID string                 `json:"document_id,omitempty" validate:"omitempty,printascii,max=100"`
	Content    string                 `json:"content,omitempty"     validate:"required_without=Embedding,omitempty"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
	Embedding  []float32              `json:"embedding,omitempty"   validate:"required_without=Content,omitempty"`
}

type UpdateDocumentRequest struct {
	DocumentID string                 `json:"document_id"        validate:"printascii,max=40,omitempty"`
	Metadata   map[string]interface{} `json:"metadata,omitempty" validate:"omitempty"`
}

type UpdateDocumentListRequest struct {
	UUID uuid.UUID `json:"uuid" validate:"required"`
	UpdateDocumentRequest
}

type GetDocumentRequest struct {
	UUID       uuid.UUID `json:"uuid"        validate:"required_without=DocumentID,uuid,omitempty"`
	DocumentID string    `json:"document_id" validate:"required_without=UUID,alphanum,max=40,omitempty"`
}

type GetDocumentListRequest struct {
	UUIDs       []uuid.UUID `json:"uuids"        validate:"required_without=DocumentIDs"`
	DocumentIDs []string    `json:"document_ids" validate:"required_without=UUIDs"`
}

type DocumentResponse struct {
	UUID       uuid.UUID              `json:"uuid"`
	CreatedAt  time.Time              `json:"created_at"`
	UpdatedAt  time.Time              `json:"updated_at"`
	DocumentID string                 `json:"document_id"`
	Content    string                 `json:"content"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
	Embedding  []float32              `json:"embedding"`
	IsEmbedded bool                   `json:"is_embedded"`
}

type DocEmbeddingTask struct {
	UUID           uuid.UUID `json:"uuid"`
	CollectionName string    `json:"collection_name"`
	Content        string    `json:"content"`
}

type DocEmbeddingUpdate struct {
	UUID           uuid.UUID `json:"uuid"`
	CollectionName string    `json:"collection_name"`
	ProcessedAt    time.Time `json:"time"`
	Embedding      []float32 `json:"embedding,omitempty" bun:"type:vector,nullzero"`
}
