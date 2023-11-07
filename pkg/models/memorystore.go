package models

import (
	"context"

	"github.com/google/uuid"
)

// MemoryStore interface
type MemoryStore[T any] interface {
	MemoryStorer
	MessageStorer
	SessionStorer
	SummaryStorer
	// PurgeDeleted hard deletes all deleted data in the MemoryStore.
	PurgeDeleted(ctx context.Context) error
	// Close is called when the application is shutting down. This is a good place to clean up any resources used by
	// the MemoryStore implementation.
	Close() error
}

type SessionStorer interface {
	// CreateSession creates a new Session for a given sessionID.
	CreateSession(
		ctx context.Context,
		appState *AppState,
		session *CreateSessionRequest,
	) (*Session, error)
	// GetSession retrieves a Session for a given sessionID.
	GetSession(
		ctx context.Context,
		appState *AppState,
		sessionID string,
	) (*Session, error)
	// UpdateSession updates a Session for a given sessionID. Omly the metadata is updated.
	UpdateSession(
		ctx context.Context,
		appState *AppState,
		session *UpdateSessionRequest,
	) (*Session, error)
	// DeleteSession deletes all records for a given sessionID. This is a soft delete. Related Messages
	// and MessageEmbeddings are also soft deleted.
	DeleteSession(ctx context.Context, sessionID string) error
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
}

type MessageStorer interface {
	// GetMessagesByUUID retrieves messages for a given sessionID and UUID slice.
	GetMessagesByUUID(
		ctx context.Context,
		appState *AppState,
		sessionID string,
		uuids []uuid.UUID,
	) ([]Message, error)
	// GetMessageList retrieves a list of messages for a given sessionID. Paginated by cursor and limit.
	GetMessageList(ctx context.Context,
		appState *AppState,
		sessionID string,
		pageNumber int,
		pageSize int,
	) (*MessageListResponse, error)
	// PutMessageMetadata creates, updates, or deletes metadata for a given message, and does not
	// update the message itself.
	// isPrivileged indicates whether the caller is privileged to add or update system metadata.
	PutMessageMetadata(ctx context.Context,
		appState *AppState,
		sessionID string,
		messages []Message,
		isPrivileged bool) error
	// PutMessageEmbeddings stores a collection of TextData for a given sessionID.
	PutMessageEmbeddings(ctx context.Context,
		appState *AppState,
		sessionID string,
		embeddings []TextData) error
	// GetMessageEmbeddings retrieves a collection of TextData for a given sessionID.
	GetMessageEmbeddings(ctx context.Context,
		appState *AppState,
		sessionID string) ([]TextData, error)
}

type MemoryStorer interface {
	// GetMemory returns memory for a given sessionID.
	// If config.Type is SimpleMemoryType, returns the most recent Summary and a list of messages.
	// If config.Type is PerpetualMemoryType, returns the last X messages, optionally the most recent summary
	// and a list of summaries semantically similar to the most recent messages.
	GetMemory(ctx context.Context,
		appState *AppState,
		config *MemoryConfig) (*Memory, error)
	// PutMemory stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
	PutMemory(ctx context.Context,
		appState *AppState,
		sessionID string,
		memoryMessages *Memory,
		skipNotify bool) error // skipNotify is used to prevent loops when calling NotifyExtractors.
	// SearchMemory retrieves a collection of SearchResults for a given sessionID and query. Currently, The
	// MemorySearchResult structure can include both Messages and Summaries. Currently, we only search Messages.
	SearchMemory(
		ctx context.Context,
		appState *AppState,
		sessionID string,
		query *MemorySearchPayload,
		limit int) ([]MemorySearchResult, error)
}

type SummaryStorer interface {
	// GetSummary retrieves the most recent Summary for a given sessionID. The Summary return includes the UUID of the
	// SummaryPoint, which the most recent Message in the collection of messages that was used to generate the Summary.
	GetSummary(ctx context.Context,
		appState *AppState,
		sessionID string) (*Summary, error)
	GetSummaryByUUID(ctx context.Context,
		appState *AppState,
		sessionID string,
		uuid uuid.UUID) (*Summary, error)
	// GetSummaryList retrieves a list of Summary for a given sessionID. Paginated by cursor and limit.
	GetSummaryList(ctx context.Context,
		appState *AppState,
		sessionID string,
		pageNumber int,
		pageSize int,
	) (*SummaryListResponse, error)
	// PutSummary stores a new Summary for a given sessionID.
	PutSummary(ctx context.Context,
		appState *AppState,
		sessionID string,
		summary *Summary) error
	// UpdateSummaryMetadata updates the metadata for a given Summary. The Summary UUID must be set.
	UpdateSummaryMetadata(ctx context.Context,
		appState *AppState,
		summary *Summary) error
	// PutSummaryEmbedding stores a TextData for a given sessionID and Summary UUID.
	PutSummaryEmbedding(ctx context.Context,
		appState *AppState,
		sessionID string,
		embedding *TextData) error
}
