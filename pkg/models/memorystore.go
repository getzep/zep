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
		session *CreateSessionRequest,
	) (*Session, error)
	// GetSession retrieves a Session for a given sessionID.
	GetSession(
		ctx context.Context,
		sessionID string,
	) (*Session, error)
	// UpdateSession updates a Session for a given sessionID. Omly the metadata is updated.
	UpdateSession(
		ctx context.Context,
		session *UpdateSessionRequest,
	) (*Session, error)
	// DeleteSession deletes all records for a given sessionID. This is a soft delete. Related Messages
	// and MessageEmbeddings are also soft deleted.
	DeleteSession(ctx context.Context, sessionID string) error
	// ListSessions returns a list of all Sessions, paginated by cursor and limit.
	ListSessions(
		ctx context.Context,
		cursor int64,
		limit int,
	) ([]*Session, error)
	// ListSessionsOrdered returns an ordered list of all Sessions, paginated by pageNumber and pageSize, and
	// the total count of all sessions.
	// orderedBy is the column to order by. asc is a boolean indicating whether to order ascending or descending.
	ListSessionsOrdered(
		ctx context.Context,
		pageNumber int,
		pageSize int,
		orderedBy string,
		asc bool,
	) (*SessionListResponse, error)
}

type MessageStorer interface {
	// UpdateMessages updates a collection of Messages for a given sessionID. If includeContent is true, the
	// role and content fields are updated, too. If isPrivileged is true, the `system` key may be updated.
	UpdateMessages(
		ctx context.Context,
		sessionID string,
		messages []Message,
		isPrivileged bool,
		includeContent bool) error
	// GetMessagesByUUID retrieves messages for a given sessionID and UUID slice.
	GetMessagesByUUID(
		ctx context.Context,
		sessionID string,
		uuids []uuid.UUID,
	) ([]Message, error)
	// GetMessageList retrieves a list of messages for a given sessionID. Paginated by cursor and limit.
	GetMessageList(ctx context.Context,
		sessionID string,
		pageNumber int,
		pageSize int,
	) (*MessageListResponse, error)
	// CreateMessageEmbeddings stores a collection of TextData for a given sessionID.
	CreateMessageEmbeddings(ctx context.Context,
		sessionID string,
		embeddings []TextData) error
	// GetMessageEmbeddings retrieves a collection of TextData for a given sessionID.
	GetMessageEmbeddings(ctx context.Context,
		sessionID string) ([]TextData, error)
}

type MemoryStorer interface {
	// GetMemory returns the most recent Summary and a list of messages for a given sessionID.
	// GetMemory returns:
	//   - the most recent Summary, if one exists
	//   - the lastNMessages messages, if lastNMessages > 0
	//   - all messages since the last SummaryPoint, if lastNMessages == 0
	//   - if no Summary (and no SummaryPoint) exists and lastNMessages == 0, returns
	//     all undeleted messages
	GetMemory(ctx context.Context,
		sessionID string,
		lastNMessages int) (*Memory, error)
	// PutMemory stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
	PutMemory(ctx context.Context,
		sessionID string,
		memoryMessages *Memory,
		skipNotify bool) error // skipNotify is used to prevent loops when calling NotifyExtractors.
	// SearchMemory retrieves a collection of SearchResults for a given sessionID and query. Currently, The
	// MemorySearchResult structure can include both Messages and Summaries.
	SearchMemory(
		ctx context.Context,
		sessionID string,
		query *MemorySearchPayload,
		limit int) ([]MemorySearchResult, error)
}

type SummaryStorer interface {
	// GetSummary retrieves the most recent Summary for a given sessionID. The Summary return includes the UUID of the
	// SummaryPoint, which the most recent Message in the collection of messages that was used to generate the Summary.
	GetSummary(ctx context.Context,
		sessionID string) (*Summary, error)
	GetSummaryByUUID(ctx context.Context,
		sessionID string,
		uuid uuid.UUID) (*Summary, error)
	// GetSummaryList retrieves a list of Summary for a given sessionID. Paginated by cursor and limit.
	GetSummaryList(ctx context.Context,
		sessionID string,
		pageNumber int,
		pageSize int,
	) (*SummaryListResponse, error)
	// CreateSummary stores a new Summary for a given sessionID.
	CreateSummary(ctx context.Context,
		sessionID string,
		summary *Summary) error
	// UpdateSummary updates the metadata for a given Summary. The Summary UUID must be set.
	UpdateSummary(ctx context.Context,
		sessionID string,
		summary *Summary,
		includeContent bool,
	) error
	// PutSummaryEmbedding stores a TextData for a given sessionID and Summary UUID.
	PutSummaryEmbedding(ctx context.Context,
		sessionID string,
		embedding *TextData) error
}
