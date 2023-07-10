package models

import (
	"context"

	"github.com/google/uuid"
)

// MemoryStore interface
type MemoryStore[T any] interface {
	// GetMemory returns the most recent Summary and a list of messages for a given sessionID.
	// GetMemory returns:
	//   - the most recent Summary, if one exists
	//   - the lastNMessages messages, if lastNMessages > 0
	//   - all messages since the last SummaryPoint, if lastNMessages == 0
	//   - if no Summary (and no SummaryPoint) exists and lastNMessages == 0, returns
	//     all undeleted messages
	GetMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		lastNMessages int) (*Memory, error)
	// GetSummary retrieves the most recent Summary for a given sessionID. The Summary return includes the UUID of the
	// SummaryPoint, which the most recent Message in the collection of messages that was used to generate the Summary.
	GetSummary(ctx context.Context,
		appState *AppState,
		sessionID string) (*Summary, error)
	// PutMemory stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
	PutMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		memoryMessages *Memory,
		skipNotify bool) error // skipNotify is used to prevent loops when calling NotifyExtractors.
	// PutSummary stores a new Summary for a given sessionID.
	PutSummary(ctx context.Context,
		appState *AppState,
		sessionID string,
		summary *Summary) error
	// PutMessageMetadata creates, updates, or deletes metadata for a given message, and does not
	// update the message itself.
	// isPrivileged indicates whether the caller is privileged to add or update system metadata.
	PutMessageMetadata(ctx context.Context,
		appState *AppState,
		sessionID string,
		messages []Message,
		isPrivileged bool) error
	// PutMessageVectors stores a collection of Embedding for a given sessionID.
	PutMessageVectors(ctx context.Context,
		appState *AppState,
		sessionID string,
		embeddings []Embedding) error
	// GetMessageVectors retrieves a collection of Embedding for a given sessionID.
	GetMessageVectors(ctx context.Context,
		appState *AppState,
		sessionID string) ([]Embedding, error)
	// SearchMemory retrieves a collection of SearchResults for a given sessionID and query. Currently, The
	// MemorySearchResult structure can include both Messages and Summaries. Currently, we only search Messages.
	SearchMemory(
		ctx context.Context,
		appState *AppState,
		sessionID string,
		query *MemorySearchPayload,
		limit int) ([]MemorySearchResult, error)
	// DeleteSession deletes all records for a given sessionID. This is a soft delete. Hard deletes will be handled
	// by a separate process or left to the implementation.
	DeleteSession(ctx context.Context, sessionID string) error
	// GetSession retrieves a Session for a given sessionID.
	GetSession(ctx context.Context, appState *AppState, sessionID string) (*Session, error)
	// PutSession creates or updates a Session for a given sessionID.
	PutSession(
		ctx context.Context,
		appState *AppState,
		session *Session,
	) error
	// PutCollection creates a new DocumentCollection.
	// If a collection with the same name already exists, it will be overwritten.
	PutCollection(
		ctx context.Context,
		appState *AppState,
		collection *DocumentCollection,
	) error
	// GetCollection retrieves a DocumentCollection by name.
	GetCollection(
		ctx context.Context,
		appState *AppState,
		collectionName string,
	) (*DocumentCollection, error)
	// GetCollectionList retrieves the list of DocumentCollection.
	GetCollectionList(
		ctx context.Context,
		appState *AppState,
	) ([]DocumentCollection, error)
	// DeleteCollection deletes a DocumentCollection by name.
	DeleteCollection(
		ctx context.Context,
		appState *AppState,
		collectionName string,
	) error
	// PutDocuments creates a batch of Documents.
	// If a document with the same UUID already exists, it will be overwritten.
	PutDocuments(
		ctx context.Context,
		appState *AppState,
		documents *[]Document,
	) error
	// GetDocument retrieves a Document by UUID.
	GetDocument(
		ctx context.Context,
		appState *AppState,
		documentUUID uuid.UUID,
	) (*Document, error)
	// DeleteDocument deletes a Document by UUID.
	DeleteDocument(
		ctx context.Context,
		appState *AppState,
		documentUUID uuid.UUID,
	) error
	// SearchDocuments retrieves a collection of DocumentSearchResultPage based on the provided search query.
	// It accepts an optional limit for the total number of results, as well as parameters for pagination: pageNumber and pageSize.
	// Parameters:
	// - limit: Defines the maximum number of results returned. If it's 0, all the results will be returned.
	// - pageNumber: Specifies the current page number in the pagination scheme.
	// - pageSize: Determines the number of results per page. If it's -1, all results are returned on a single page.
	// The mmr parameter is used to enable/disable the MMR algorithm for search results.
	// The function will return the results in pages as determined by pageSize.
	SearchDocuments(
		ctx context.Context,
		appState *AppState,
		collectionName string,
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
	Attach(observer Extractor)
	// NotifyExtractors notifies all registered Extractors of a new MessageEvent.
	NotifyExtractors(
		ctx context.Context,
		appState *AppState,
		eventData *MessageEvent,
	)
	// PurgeDeleted hard deletes all deleted data in the MemoryStore.
	PurgeDeleted(ctx context.Context) error
	// Close is called when the application is shutting down. This is a good place to clean up any resources used by
	// the MemoryStore implementation.
	Close() error
}
