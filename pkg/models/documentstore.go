package models

import (
	"context"

	"github.com/google/uuid"
)

// DocumentStore interface
type DocumentStore[T any] interface {
	// CreateCollection creates a new DocumentCollection.
	// If a collection with the same name already exists, it will be overwritten.
	CreateCollection(
		ctx context.Context,
		collection DocumentCollection,
	) error
	UpdateCollection(
		ctx context.Context,
		collection DocumentCollection,
	) error
	// GetCollection retrieves a DocumentCollection by name.
	GetCollection(
		ctx context.Context,
		collectionName string,
	) (DocumentCollection, error)
	// GetCollectionList retrieves the list of DocumentCollection.
	GetCollectionList(
		ctx context.Context,
	) ([]DocumentCollection, error)
	// DeleteCollection deletes a DocumentCollection by name.
	DeleteCollection(
		ctx context.Context,
		collectionName string,
	) error
	// CreateDocuments creates a batch of Documents.
	CreateDocuments(
		ctx context.Context,
		collectionName string,
		documents []Document,
	) ([]uuid.UUID, error)
	// UpdateDocuments updates a batch of Documents.
	// The provided Document UUIDs must match existing documents.
	UpdateDocuments(
		ctx context.Context,
		collectionName string,
		documents []Document,
	) error
	// GetDocuments retrieves a Document by UUID.
	GetDocuments(
		ctx context.Context,
		collectionName string,
		uuids []uuid.UUID,
		DocumentID []string,
	) ([]Document, error)
	// DeleteDocuments deletes a Document by UUID.
	DeleteDocuments(
		ctx context.Context,
		collectionName string,
		documentUUIDs []uuid.UUID,
	) error
	// SearchCollection retrieves a collection of DocumentSearchResultPage based on the provided search query.
	// It accepts an optional limit for the total number of results, as well as parameters for pagination: pageNumber and pageSize.
	// Parameters:
	// - limit: Defines the maximum number of results returned. If it's 0, all the results will be returned.
	// - pageNumber: Specifies the current page number in the pagination scheme.
	// - pageSize: Determines the number of results per page. If it's -1, all results are returned on a single page.
	// The mmr parameter is used to enable/disable the MMR algorithm for search results.
	// The function will return the results in pages as determined by pageSize.
	SearchCollection(
		ctx context.Context,
		query *DocumentSearchPayload,
		limit int,
		pageNumber int,
		pageSize int,
	) (*DocumentSearchResultPage, error)
	// CreateCollectionIndex creates an index on the collection. Manually calling this function will drop and
	// recreate the index, if it exists.
	// force: If true, the index will be created even if there are too few documents in the collection.
	CreateCollectionIndex(ctx context.Context, collectionName string, force bool) error
	// OnStart is called when the application starts. This is a good place to initialize any resources or configs that
	// are required by the MemoryStore implementation.
	OnStart(ctx context.Context) error
	// Shutdown is called when the application is shutting down. This is a good place to clean up any resources or configs
	Shutdown(ctx context.Context) error
	// GetClient returns the underlying storage client
	GetClient() any
}
