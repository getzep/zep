package postgres

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"errors"

	"github.com/getzep/zep/pkg/store"

	"github.com/getzep/zep/internal"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

var log = internal.GetLogger()

// NewPostgresMemoryStore returns a new PostgresMemoryStore. Use this to correctly initialize the store.
func NewPostgresMemoryStore(
	appState *models.AppState,
	client *bun.DB,
) (*PostgresMemoryStore, error) {
	if appState == nil {
		return nil, store.NewStorageError("nil appState received", nil)
	}

	pms := &PostgresMemoryStore{store.BaseMemoryStore[*bun.DB]{Client: client}}
	err := pms.OnStart(context.Background(), appState)
	if err != nil {
		return nil, store.NewStorageError("failed to run OnInit", err)
	}
	return pms, nil
}

// Force compiler to validate that PostgresMemoryStore implements the MemoryStore interface.
var _ models.MemoryStore[*bun.DB] = &PostgresMemoryStore{}

type PostgresMemoryStore struct {
	store.BaseMemoryStore[*bun.DB]
}

func (pms *PostgresMemoryStore) OnStart(
	_ context.Context,
	appState *models.AppState,
) error {
	err := ensurePostgresSetup(context.Background(), appState, pms.Client)
	if err != nil {
		return store.NewStorageError("failed to ensure postgres schema setup", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetClient() *bun.DB {
	return pms.Client
}

// GetSession retrieves a Session for a given sessionID.
func (pms *PostgresMemoryStore) GetSession(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
) (*models.Session, error) {
	return getSession(ctx, pms.Client, sessionID)
}

// PutSession creates or updates a Session for a given sessionID.
func (pms *PostgresMemoryStore) PutSession(
	ctx context.Context,
	_ *models.AppState,
	session *models.Session,
) error {
	_, err := putSession(ctx, pms.Client, session.SessionID, session.Metadata, false)
	return err
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
		return nil, store.NewStorageError("nil appState received", nil)
	}

	if lastNMessages < 0 {
		return nil, store.NewStorageError("cannot specify negative lastNMessages", nil)
	}

	// Get the most recent summary
	summary, err := getSummary(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, store.NewStorageError("failed to get summary", err)
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
		return nil, store.NewStorageError("failed to get messages", err)
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
		return nil, store.NewStorageError("failed to get summary", err)
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
		return store.NewStorageError("nil appState received", nil)
	}

	messageResult, err := putMessages(
		ctx,
		pms.Client,
		sessionID,
		memoryMessages.Messages,
	)
	if err != nil {
		return store.NewStorageError("failed to Create messages", err)
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
		return store.NewStorageError("failed to Create summary", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) PutMessageMetadata(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	messages []models.Message,
	isPrivileged bool,
) error {
	_, err := putMessageMetadata(ctx, pms.Client, sessionID, messages, isPrivileged)
	if err != nil {
		return store.NewStorageError("failed to Create message metadata", err)
	}
	return nil
}

func (pms *PostgresMemoryStore) SearchMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	query *models.MemorySearchPayload,
	limit int,
) ([]models.MemorySearchResult, error) {
	searchResults, err := searchMessages(ctx, appState, pms.Client, sessionID, query, limit)
	return searchResults, err
}

func (pms *PostgresMemoryStore) Close() error {
	if pms.Client != nil {
		return pms.Client.Close()
	}
	return nil
}

// DeleteSession deletes a session from the memory store. This is a soft Delete.
// TODO: A hard Delete will be implemented as an out-of-band process or left to the implementer.
func (pms *PostgresMemoryStore) DeleteSession(ctx context.Context, sessionID string) error {
	return deleteSession(ctx, pms.Client, sessionID)
}

func (pms *PostgresMemoryStore) PutMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
	embeddings []models.MessageEmbedding,
) error {
	if embeddings == nil {
		return store.NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return store.NewStorageError("no embeddings received", nil)
	}

	err := putMessageEmbeddings(ctx, pms.Client, sessionID, embeddings)
	if err != nil {
		return store.NewStorageError("failed to Create embeddings", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
) ([]models.MessageEmbedding, error) {
	embeddings, err := getMessageEmbeddings(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, store.NewStorageError("GetMessageVectors failed to get embeddings", err)
	}

	return embeddings, nil
}

func (pms *PostgresMemoryStore) PurgeDeleted(ctx context.Context) error {
	err := purgeDeleted(ctx, pms.Client)
	if err != nil {
		return store.NewStorageError("failed to purge deleted", err)
	}

	return nil
}

// acquireAdvisoryXactLock acquires a PostgreSQL advisory lock for the given key.
// Expects a transaction to be open in tx.
// `pg_advisory_xact_lock` will wait until the lock is available. The lock is released
// when the transaction is committed or rolled back.
func acquireAdvisoryXactLock(ctx context.Context, tx bun.Tx, key string) error {
	hasher := sha256.New()
	hasher.Write([]byte(key))
	hash := hasher.Sum(nil)
	lockID := binary.BigEndian.Uint64(hash[:8])

	if _, err := tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock(?)", lockID); err != nil {
		return store.NewStorageError("failed to acquire advisory lock", err)
	}

	return nil
}

// acquireAdvisoryLock acquires a PostgreSQL advisory lock for the given key.
// The lock needs to be released manually by calling releaseAdvisoryLock.
// Accepts a bun.IDB, which can be either a *bun.DB or *bun.Tx.
// Returns the lock ID.
func acquireAdvisoryLock(ctx context.Context, db bun.IDB, key string) (uint64, error) {
	hasher := sha256.New()
	hasher.Write([]byte(key))
	hash := hasher.Sum(nil)
	lockID := binary.BigEndian.Uint64(hash[:8])

	if _, err := db.ExecContext(ctx, "SELECT pg_advisory_lock(?)", lockID); err != nil {
		return 0, store.NewStorageError("failed to acquire advisory lock", err)
	}

	return lockID, nil
}

// releaseAdvisoryLock releases a PostgreSQL advisory lock for the given key.
// Accepts a bun.IDB, which can be either a *bun.DB or *bun.Tx.
func releaseAdvisoryLock(ctx context.Context, db bun.IDB, lockID uint64) error {
	if _, err := db.ExecContext(ctx, "SELECT pg_advisory_unlock(?)", lockID); err != nil {
		return store.NewStorageError("failed to release advisory lock", err)
	}

	return nil
}

// rollbackOnError rolls back the transaction if an error is encountered.
// If the error is sql.ErrTxDone, the transaction has already been committed or rolled back
// and we ignore the error.
func rollbackOnError(tx bun.Tx) {
	if rollBackErr := tx.Rollback(); rollBackErr != nil && !errors.Is(rollBackErr, sql.ErrTxDone) {
		log.Error("failed to rollback transaction", rollBackErr)
	}
}
