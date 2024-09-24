package models

import (
	"context"

	"github.com/google/uuid"
)

type MemoryStoreCommon interface {
	MemoryStorer
	MessageStorer
	SessionStorer
	PurgeDeleted(ctx context.Context, schemaName string) error
	PutMessages(ctx context.Context, sessionID string, messages []Message) ([]Message, error)
}

type SessionStorerCommon interface {
	CreateSession(ctx context.Context, session *CreateSessionRequest) (*Session, error)
	GetSession(ctx context.Context, sessionID string) (*Session, error)
	UpdateSession(ctx context.Context, session *UpdateSessionRequest, isPrivileged bool) (*Session, error)
	DeleteSession(ctx context.Context, sessionID string) error
	ListSessions(ctx context.Context, cursor int64, limit int) ([]*Session, error)
	ListSessionsOrdered(
		ctx context.Context,
		pageNumber, pageSize int,
		orderedBy string,
		asc bool,
	) (*SessionListResponse, error)
}

type MessageStorerCommon interface {
	GetMessagesLastN(ctx context.Context, sessionID string, lastNMessages int, beforeUUID uuid.UUID) ([]Message, error)
	GetMessagesByUUID(ctx context.Context, sessionID string, uuids []uuid.UUID) ([]Message, error)
	GetMessageList(ctx context.Context, sessionID string, pageNumber, pageSize int) (*MessageListResponse, error)
	UpdateMessages(ctx context.Context, sessionID string, messages []Message, isPrivileged, includeContent bool) error
}

type MemoryStorerCommon interface {
	GetMemory(ctx context.Context, sessionID string, lastNmessages int, opts ...MemoryFilterOption) (*Memory, error)
	// PutMemory stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
	PutMemory(ctx context.Context, sessionID string, memoryMessages *Memory, skipNotify bool) error // skipNotify is used to prevent loops when calling NotifyExtractors.
	SearchSessions(ctx context.Context, query *SessionSearchQuery, limit int) (*SessionSearchResponse, error)
}
