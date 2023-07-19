package models

import (
	"time"

	"github.com/google/uuid"
)

type DocumentCollection struct {
	UUID                uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"                     json:"uuid"`
	CreatedAt           time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" json:"created_at"`
	UpdatedAt           time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"         json:"updated_at"`
	Name                string                 `bun:",notnull,unique"                                             json:"name"`
	Description         string                 `bun:",notnull"                                                    json:"description"`
	Metadata            map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"                         json:"metadata,omitempty"`
	TableName           string                 `bun:",notnull"                                                    json:"table_name"`
	EmbeddingModelName  string                 `bun:",notnull"                                                    json:"embedding_model_name"`
	EmbeddingDimensions int                    `bun:",notnull"                                                    json:"embedding_dimensions"`
	DistanceFunction    string                 `bun:",notnull"                                                    json:"distance_function"` // Distance function to use for index
	IsNormalized        bool                   `bun:",notnull"                                                    json:"is_normalized"`     // Are the embeddings normalized?
	IsIndexed           bool                   `bun:",notnull"                                                    json:"is_indexed"`        // Has an index been created on the collection table?
}

type DocumentBase struct {
	UUID           uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"                     json:"uuid"`
	CreatedAt      time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" json:"created_at"`
	UpdatedAt      time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"         json:"updated_at"`
	DeletedAt      time.Time              `bun:"type:timestamptz,soft_delete,nullzero"                       json:"deleted_at"`
	DocumentID     string                 `bun:",unique"                                                     json:"document_id"`
	Content        string                 `bun:""                                                            json:"content"`
	Metadata       map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"                         json:"metadata,omitempty"`
	CollectionUUID uuid.UUID              `                                                                  json:"collection_uuid"`
}

type Document struct {
	DocumentBase
	Embedding []float32 `bun:"type:real[]" json:"embedding"`
}
