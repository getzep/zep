package models

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

type DocumentCollectionInterface interface {
	Put(ctx context.Context) error
	GetByName(ctx context.Context) error
	GetAll(ctx context.Context) ([]DocumentCollectionInterface, error)
	Delete(ctx context.Context) error
	PutDocuments(
		ctx context.Context,
		documents []DocumentInterface,
	) error
	GetDocuments(ctx context.Context,
		limit int,
		documents []DocumentInterface,
	) error
	DeleteDocumentByUUID(
		ctx context.Context,
		documentUUID uuid.UUID,
	) error
	PutDocumentEmbeddings(
		ctx context.Context,
		documentEmbeddings []DocumentInterface,
	) error
}

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

func (dc *DocumentCollection) Put(ctx context.Context) error {
	_ = ctx
	return errors.New("not implemented")
}

func (dc *DocumentCollection) GetByName(ctx context.Context) error {
	_ = ctx
	return errors.New("not implemented")
}

func (dc *DocumentCollection) GetAll(ctx context.Context) ([]DocumentCollectionInterface, error) {
	_ = ctx
	return nil, errors.New("not implemented")
}

func (dc *DocumentCollection) Delete(ctx context.Context) error {
	_ = ctx
	return errors.New("not implemented")
}

func (dc *DocumentCollection) PutDocuments(
	ctx context.Context,
	documents []DocumentInterface) error {
	_ = ctx
	_ = documents
	return errors.New("not implemented")
}

func (dc *DocumentCollection) GetDocuments(ctx context.Context,
	limit int,
	documents []DocumentInterface) error {
	_ = ctx
	_ = limit
	_ = documents
	return errors.New("not implemented")
}

func (dc *DocumentCollection) DeleteDocumentByUUID(
	ctx context.Context,
	documentUUID uuid.UUID) error {
	_ = ctx
	_ = documentUUID
	return errors.New("not implemented")
}

func (dc *DocumentCollection) PutDocumentEmbeddings(
	ctx context.Context,
	documentEmbeddings []DocumentInterface) error {
	_ = ctx
	_ = documentEmbeddings
	return errors.New("not implemented")
}

var _ DocumentCollectionInterface = &DocumentCollection{}

type DocumentInterface interface {
	Marker()
}

type Document struct {
	UUID           uuid.UUID              `json:"uuid"`
	CreatedAt      time.Time              `json:"created_at"`
	Content        string                 `json:"content"`
	Metadata       map[string]interface{} `json:"metadata,omitempty"`
	CollectionUUID uuid.UUID              `json:"collection_uuid"`
	Embedding      []float32              `json:"embedding"`
}

func (d *Document) Marker() {}
