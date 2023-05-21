package memorystore

import (
	"context"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

// NewPostgresMemoryStore returns a new PostgresMemoryStore. Use this to correctly initialize the store.
func NewPostgresMemoryStore(
	appState *models.AppState,
	client *bun.DB,
) (*PostgresMemoryStore, error) {
	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	pms := &PostgresMemoryStore{BaseMemoryStore[*bun.DB]{Client: client}}
	err := pms.OnStart(context.Background(), appState)
	if err != nil {
		return nil, NewStorageError("failed to run OnInit", err)
	}
	return pms, nil
}

// Force compiler to validate that PostgresMemoryStore implements the MemoryStore interface.
var _ models.MemoryStore[*bun.DB] = &PostgresMemoryStore{}

type PostgresMemoryStore struct {
	BaseMemoryStore[*bun.DB]
}

func (pms *PostgresMemoryStore) OnStart(
	_ context.Context,
	_ *models.AppState,
) error {
	err := ensurePostgresSetup(context.Background(), pms.Client)
	if err != nil {
		return NewStorageError("failed to ensure postgres schema setup", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetClient() *bun.DB {
	return pms.Client
}

// GetMemory returns the most recent Summary and a list of messages for a given sessionID.
// GetMemory returns:
//   - the most recent Summary, if one exists
//   - the lastNMessages messages, if lastNMessages > 0
//   - all messages since the last SummaryPoint, if lastNMessages == 0
//   - if no Summary (and no SummaryPoint) exists and lastNMessages == 0, returns
//     all undeleted messages
func (pms *PostgresMemoryStore) GetMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	lastNMessages int,
) (*models.Memory, error) {
	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	err := checkLastNParms(0, lastNMessages)
	if err != nil {
		return nil, NewStorageError("invalid lastNMessages or lastNTokens in get call", err)
	}

	// Get the most recent summary
	summary, err := getSummary(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("failed to get summary", err)
	}
	if summary != nil {
		log.Debugf("Got summary for %s: %s", sessionID, summary.UUID)
	}

	messages, err := getMessages(
		ctx,
		pms.Client,
		sessionID,
		appState.Config.Memory.MessageWindow,
		summary,
		lastNMessages,
	)
	if err != nil {
		return nil, NewStorageError("failed to get messages", err)
	}
	if messages != nil {
		log.Debugf("Got messages for %s: %d", sessionID, len(messages))
	}

	memory := models.Memory{
		Messages: messages,
		Summary:  summary,
	}

	return &memory, nil
}

func (pms *PostgresMemoryStore) GetSummary(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
) (*models.Summary, error) {
	summary, err := getSummary(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("failed to get summary", err)
	}

	return summary, nil
}

func (pms *PostgresMemoryStore) PutMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	memoryMessages *models.Memory,
	skipNotify bool,
) error {
	if appState == nil {
		return NewStorageError("nil appState received", nil)
	}

	messageResult, err := putMessages(
		ctx,
		pms.Client,
		sessionID,
		memoryMessages.Messages,
	)
	if err != nil {
		return NewStorageError("failed to put messages", err)
	}

	if skipNotify {
		return nil
	}

	pms.NotifyExtractors(
		context.Background(),
		appState,
		&models.MessageEvent{SessionID: sessionID,
			Messages: messageResult},
	)

	return nil
}

func (pms *PostgresMemoryStore) PutSummary(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	summary *models.Summary,
) error {
	_, err := putSummary(ctx, pms.Client, sessionID, summary)
	if err != nil {
		return NewStorageError("failed to put summary", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) SearchMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	query *models.SearchPayload,
	limit int,
) ([]models.SearchResult, error) {
	searchResults, err := searchMessages(ctx, appState, pms.Client, sessionID, query, limit)
	return searchResults, err
}

func (pms *PostgresMemoryStore) Close() error {
	if pms.Client != nil {
		return pms.Client.Close()
	}
	return nil
}

// DeleteSession deletes a session from the memory store. This is a soft delete.
// TODO: A hard delete will be implemented as an out-of-band process or left to the implementer.
func (pms *PostgresMemoryStore) DeleteSession(ctx context.Context, sessionID string) error {
	return deleteSession(ctx, pms.Client, sessionID)
}

func (pms *PostgresMemoryStore) PutMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
	embeddings []models.Embeddings,
) error {
	if embeddings == nil {
		return NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return NewStorageError("no embeddings received", nil)
	}

	err := putEmbeddings(ctx, pms.Client, sessionID, embeddings)
	if err != nil {
		return NewStorageError("failed to put embeddings", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
) ([]models.Embeddings, error) {
	embeddings, err := getMessageVectors(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("GetMessageVectors failed to get embeddings", err)
	}

	return embeddings, nil
}
