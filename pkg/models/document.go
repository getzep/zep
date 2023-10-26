package models

import (
	"time"

	"github.com/google/uuid"
)

type IndexType string
type DistanceFunction string

/* Collection  Models */

type DocumentCollection struct {
	UUID                      uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"                     yaml:"uuid"`
	CreatedAt                 time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" yaml:"created_at"`
	UpdatedAt                 time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"         yaml:"updated_at"`
	Name                      string                 `bun:",notnull,unique"                                             yaml:"name"`
	Description               string                 `bun:",notnull"                                                    yaml:"description"`
	Metadata                  map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"                         yaml:"metadata"`
	TableName                 string                 `bun:",notnull"                                                    yaml:"table_name"`
	EmbeddingModelName        string                 `bun:",notnull"                                                    yaml:"embedding_model_name"`
	EmbeddingDimensions       int                    `bun:",notnull"                                                    yaml:"embedding_dimensions"`
	IsAutoEmbedded            bool                   `bun:",notnull"                                                    yaml:"is_auto_embedded"`  // Is the collection automatically embedded by Zep?
	DistanceFunction          DistanceFunction       `bun:",notnull"                                                    yaml:"distance_function"` // Distance function to use for index
	IsNormalized              bool                   `bun:",notnull"                                                    yaml:"is_normalized"`     // Are the embeddings normalized?
	IsIndexed                 bool                   `bun:",notnull"                                                    yaml:"is_indexed"`        // Has an index been created on the collection table?
	IndexType                 IndexType              `bun:",notnull"                                                    yaml:"index_type"`        // Type of index to use
	ListCount                 int                    `bun:",notnull"                                                    yaml:"list_count"`        // Number of lists in the collection index
	ProbeCount                int                    `bun:",notnull"                                                    yaml:"probe_count"`       // Number of probes to use when searching the index
	*DocumentCollectionCounts ` yaml:"document_collection_counts,inline"`
}

type DocumentCollectionCounts struct {
	DocumentCount         int `bun:"document_count"          json:"document_count"          yaml:"document_count,omitempty"`          // Number of documents in the collection
	DocumentEmbeddedCount int `bun:"document_embedded_count" json:"document_embedded_count" yaml:"document_embedded_count,omitempty"` // Number of documents with embeddings
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

type SearchDocumentResult struct {
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
	UUID uuid.UUID `json:"uuid"`
}

type DocEmbeddingUpdate struct {
	UUID           uuid.UUID `json:"uuid"`
	CollectionName string    `json:"collection_name"`
	ProcessedAt    time.Time `json:"time"`
	Embedding      []float32 `json:"embedding,omitempty" bun:"type:vector,nullzero"`
}
