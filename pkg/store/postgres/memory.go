package postgres

import (
	"context"
	"errors"
	"fmt"
	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

// NewMemoryDAO creates a new MemoryDAO.
func NewMemoryDAO(db *bun.DB, appState *models.AppState, sessionID string) (*MemoryDAO, error) {
	if sessionID == "" {
		return nil, errors.New("sessionID cannot be empty")
	}
	return &MemoryDAO{
		db:        db,
		appState:  appState,
		sessionID: sessionID,
	}, nil
}

// MemoryDAO is a data access object for Memory. A Memory is an overlay over Messages and Summaries. It is used to
// retrieve a set of messages and a summary for a given sessionID, to store a new set of messages from
// a chat client, and to search for messages and summaries.
type MemoryDAO struct {
	db        *bun.DB
	appState  *models.AppState
	sessionID string
}

// Get returns the most recent Summary and a list of messages for a given sessionID.
// Get returns:
//   - the most recent Summary, if one exists
//   - the lastNMessages messages, if lastNMessages > 0
//   - all messages since the last SummaryPoint, if lastNMessages == 0
//   - if no Summary (and no SummaryPoint) exists and lastNMessages == 0, returns
//     all undeleted messages up to the configured message window
func (m *MemoryDAO) Get(
	ctx context.Context,
	lastNMessages int,
) (*models.Memory, error) {
	if lastNMessages < 0 {
		return nil, errors.New("cannot specify negative lastNMessages")
	}

	summaryDAO, err := NewSummaryDAO(m.db, m.appState, m.sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create summaryDAO: %w", err)
	}
	summary, err := summaryDAO.Get(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get summary: %w", err)
	}

	messageDAO, err := NewMessageDAO(m.db, m.appState, m.sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create messageDAO: %w", err)
	}

	var messages []models.Message
	if lastNMessages > 0 {
		messages, err = messageDAO.GetLastN(ctx, lastNMessages, uuid.Nil)
	} else {
		memoryWindow := m.appState.Config.Memory.MessageWindow
		messages, err = messageDAO.GetSinceLastSummary(ctx, summary, memoryWindow)
	}
	if err != nil {
		return nil, fmt.Errorf("failed to get messages: %w", err)
	}

	memory := models.Memory{
		Messages: messages,
		Summary:  summary,
	}

	return &memory, nil
}

// Create stores a Memory for a given sessionID. If the SessionID doesn't exist, a new one is created.
// If skipNotify is true, the new messages will not be published to the message queue router.
func (m *MemoryDAO) Create(
	ctx context.Context,
	memoryMessages *models.Memory,
	skipNotify bool,
) error {
	// Try update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(m.db)
	_, err := sessionStore.Update(ctx, &models.UpdateSessionRequest{
		SessionID: m.sessionID,
	}, false)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			_, err = sessionStore.Create(ctx, &models.CreateSessionRequest{
				SessionID: m.sessionID,
			})
			if err != nil {
				return err
			}
		} else {
			return err
		}
	}

	messageDAO, err := NewMessageDAO(m.db, m.appState, m.sessionID)
	if err != nil {
		return fmt.Errorf("failed to create messageDAO: %w", err)
	}

	messageResult, err := messageDAO.CreateMany(ctx, memoryMessages.Messages)
	if err != nil {
		return fmt.Errorf("failed to put messages: %w", err)
	}

	// If we are skipping pushing new messages to the message router, return early
	if skipNotify {
		return nil
	}

	mt := make([]models.MessageTask, len(messageResult))
	for i, message := range messageResult {
		mt[i] = models.MessageTask{UUID: message.UUID}
	}

	// Send new messages to the message router
	err = m.appState.TaskPublisher.PublishMessage(
		map[string]string{"session_id": m.sessionID},
		mt,
	)
	if err != nil {
		return fmt.Errorf("failed to publish new messages %w", err)
	}

	return nil
}

func (m *MemoryDAO) Search(
	ctx context.Context,
	query *models.MemorySearchPayload,
	limit int,
) ([]models.MemorySearchResult, error) {
	// TODO: refactor search into DAO
	searchResults, err := searchMemory(ctx, m.appState, m.db, m.sessionID, query, limit)
	return searchResults, err
}
