package models

import (
	"context"
)

// MemoryStore interface
// TODO: This needs to be broken up into smaller interfaces.
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
	// GetMessageList retrieves a list of messages for a given sessionID. Paginated by cursor and limit.
	GetMessageList(ctx context.Context,
		appState *AppState,
		sessionID string,
		pageNumber int,
		pageSize int,
	) (*MessageListResponse, error)
	// GetSummary retrieves the most recent Summary for a given sessionID. The Summary return includes the UUID of the
	// SummaryPoint, which the most recent Message in the collection of messages that was used to generate the Summary.
	GetSummary(ctx context.Context,
		appState *AppState,
		sessionID string) (*Summary, error)
	// GetSummaryList retrieves a list of Summary for a given sessionID. Paginated by cursor and limit.
	GetSummaryList(ctx context.Context,
		appState *AppState,
		sessionID string,
		pageNumber int,
		pageSize int,
	) (*SummaryListResponse, error)
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
	// PutMessageVectors stores a collection of MessageEmbedding for a given sessionID.
	PutMessageVectors(ctx context.Context,
		appState *AppState,
		sessionID string,
		embeddings []MessageEmbedding) error
	// GetMessageVectors retrieves a collection of MessageEmbedding for a given sessionID.
	GetMessageVectors(ctx context.Context,
		appState *AppState,
		sessionID string) ([]MessageEmbedding, error)
	// SearchMemory retrieves a collection of SearchResults for a given sessionID and query. Currently, The
	// MemorySearchResult structure can include both Messages and Summaries. Currently, we only search Messages.
	SearchMemory(
		ctx context.Context,
		appState *AppState,
		sessionID string,
		query *MemorySearchPayload,
		limit int) ([]MemorySearchResult, error)
	// DeleteSession deletes all records for a given sessionID. This is a soft delete. Related Messages
	// and MessageEmbeddings are also soft deleted.
	DeleteSession(ctx context.Context, sessionID string) error
	// GetSession retrieves a Session for a given sessionID.
	GetSession(
		ctx context.Context,
		appState *AppState,
		sessionID string,
	) (*Session, error)
	// CreateSession creates a new Session for a given sessionID.
	CreateSession(
		ctx context.Context,
		appState *AppState,
		session *CreateSessionRequest,
	) (*Session, error)
	// UpdateSession updates a Session for a given sessionID. Omly the metadata is updated.
	UpdateSession(
		ctx context.Context,
		appState *AppState,
		session *UpdateSessionRequest,
	) (*Session, error)
	// ListSessions returns a list of all Sessions, paginated by cursor and limit.
	ListSessions(
		ctx context.Context,
		appState *AppState,
		cursor int64,
		limit int,
	) ([]*Session, error)
	// ListSessionsOrdered returns an ordered list of all Sessions, paginated by pageNumber and pageSize, and
	// the total count of all sessions.
	// orderedBy is the column to order by. asc is a boolean indicating whether to order ascending or descending.
	ListSessionsOrdered(
		ctx context.Context,
		appState *AppState,
		pageNumber int,
		pageSize int,
		orderedBy string,
		asc bool,
	) (*SessionListResponse, error)
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
