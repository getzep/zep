package store

import (
	"context"
	"errors"
	"fmt"
	"unicode/utf8"

	"github.com/getzep/zep/lib/enablement"
	"github.com/getzep/zep/lib/telemetry"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
	"github.com/google/uuid"
)

const defaultLastNMessages = 4

func newMemoryDAO(appState *models.AppState, requestState *models.RequestState, sessionID string, lastNMessages int) *memoryDAO {
	return &memoryDAO{
		appState:      appState,
		requestState:  requestState,
		sessionID:     sessionID,
		lastNMessages: lastNMessages,
	}
}

// memoryDAO is a data access object for Memory. A Memory is an overlay over Messages. It is used to
// retrieve a set of messages for a given sessionID, to store a new set of messages from
// a chat client, and to search for messages.
type memoryDAO struct {
	appState      *models.AppState
	requestState  *models.RequestState
	sessionID     string
	lastNMessages int
}

func (dao *memoryDAO) Get(ctx context.Context, opts ...models.MemoryFilterOption) (*models.Memory, error) {
	if dao.lastNMessages < 0 {
		return nil, errors.New("lastNMessages cannot be negative")
	}

	memoryFilterOptions := models.ApplyFilterOptions(opts...)

	messageDAO := newMessageDAO(dao.appState, dao.requestState, dao.sessionID)

	// we need to get at least defaultLastNMessages messages
	mCnt := dao.lastNMessages
	if mCnt < defaultLastNMessages {
		mCnt = defaultLastNMessages
	}

	messages, err := messageDAO.GetLastN(ctx, mCnt, uuid.Nil)
	if err != nil {
		return nil, fmt.Errorf("failed to get messages: %w", err)
	}

	// return early if there are no messages
	if len(messages) == 0 {
		return &models.Memory{
			MemoryCommon: models.MemoryCommon{
				Messages: messages,
			},
		}, nil
	}

	session, err := dao.requestState.Sessions.Get(ctx, dao.sessionID)
	if err != nil {
		return nil, fmt.Errorf("get failed to get session: %w", err)
	}

	// we only want to return max dao.lastNMessages messages for chat history
	mChatHistory := messages
	if len(messages) > dao.lastNMessages {
		mChatHistory = messages[len(messages)-dao.lastNMessages:]
	}

	result, err := dao._get(ctx, session, messages, memoryFilterOptions)
	if err != nil {
		return nil, err
	}

	telemetry.I().TrackEvent(dao.requestState, telemetry.Event_GetMemory, map[string]any{
		"message_count": len(mChatHistory),
	})

	result.MemoryCommon.Messages = mChatHistory

	return result, nil
}

// Create stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
// If skipProcessing is true, the new messages will not be published to the message queue router.
func (dao *memoryDAO) Create(ctx context.Context, memoryMessages *models.Memory, skipProcessing bool) error {
	sessionStore := NewSessionDAO(dao.appState, dao.requestState)

	// Try to update the session first. If no rows are affected, create a new session.
	session, err := sessionStore.Update(ctx, &models.UpdateSessionRequest{
		UpdateSessionRequestCommon: models.UpdateSessionRequestCommon{
			SessionID: dao.sessionID,
		},
	}, false)
	if err != nil {
		if !errors.Is(err, zerrors.ErrNotFound) {
			return err
		}
		session, err = sessionStore.Create(ctx, &models.CreateSessionRequest{
			CreateSessionRequestCommon: models.CreateSessionRequestCommon{
				SessionID: dao.sessionID,
			},
		})
		if err != nil {
			return err
		}
	}

	if session.EndedAt != nil {
		return zerrors.NewSessionEndedError("session has ended")
	}

	messageDAO := newMessageDAO(dao.appState, dao.requestState, dao.sessionID)

	for _, msg := range memoryMessages.Messages {
		telemetry.I().TrackEvent(dao.requestState,
			telemetry.Event_CreateMemoryMessage,
			map[string]any{
				"message_length": utf8.RuneCountInString(msg.Content),
				"with_metadata":  len(msg.Metadata) > 0,
				"session_uuid":   session.UUID.String(),
			},
		)
		enablement.I().TrackEvent(enablement.Event_CreateMemoryMessage, dao.requestState)
	}

	messageResult, err := messageDAO.CreateMany(ctx, memoryMessages.Messages)
	if err != nil {
		return err
	}
	memoryMessages.Messages = messageResult
	// If we are skipping pushing new messages to the message router, return early
	if skipProcessing {
		return nil
	}

	err = dao._initializeProcessingMemory(ctx, session, memoryMessages)
	if err != nil {
		return fmt.Errorf("failed to initialize processing memory: %w", err)
	}

	return nil
}

func (dao *memoryDAO) SearchSessions(ctx context.Context, query *models.SessionSearchQuery, limit int) (*models.SessionSearchResponse, error) {
	return dao._searchSessions(ctx, query, limit)
}
