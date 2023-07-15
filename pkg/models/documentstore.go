package models

import (
	"context"

	"github.com/google/uuid"
)

// DocumentStore interface
type DocumentStore[T any] interface {
	// PutCollection creates a new DocumentCollection.
	// If a collection with the same name already exists, it will be overwritten.
	PutCollection(
		ctx context.Context,
		collection DocumentCollectionInterface,
	) error
	// GetCollection retrieves a DocumentCollection by name.
	GetCollection(
		ctx context.Context,
		collectionName string,
	) (DocumentCollectionInterface, error)
	// GetCollectionList retrieves the list of DocumentCollection.
	GetCollectionList(
		ctx context.Context,
	) ([]DocumentCollectionInterface, error)
	// DeleteCollection deletes a DocumentCollection by name.
	DeleteCollection(
		ctx context.Context,
		collectionName string,
	) error
	// CreateDocuments creates a batch of Documents.
	CreateDocuments(
		ctx context.Context,
		collectionName string,
		documents []DocumentInterface,
	) ([]uuid.UUID, error)
	// UpdateDocuments updates a batch of Documents.
	// The provided Document UUIDs must match existing documents.
	UpdateDocuments(
		ctx context.Context,
		collectionName string,
		documents []DocumentInterface,
	) error
	// GetDocuments retrieves a Document by UUID.
	GetDocuments(
		ctx context.Context,
		collectionName string,
		uuids []uuid.UUID,
		DocumentID []string,
	) ([]DocumentInterface, error)
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
		mmr bool, // mmr is used to enable/disable the Maximal Marginal Relevance algorithm for search results.
		pageNumber int,
		pageSize int,
	) ([]DocumentSearchResultPage, error)
	// OnStart is called when the application starts. This is a good place to initialize any resources or configs that
	// are required by the MemoryStore implementation.
	OnStart(ctx context.Context, appState *AppState) error
	// Attach is used by Extractors to register themselves with the MemoryStore. This allows the MemoryStore to notify
	// the Extractors when new occur.
}
