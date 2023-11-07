package postgres

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"errors"
	"fmt"

	"github.com/getzep/zep/pkg/store"
	"github.com/google/uuid"

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

	pms := &PostgresMemoryStore{
		BaseMemoryStore: store.BaseMemoryStore[*bun.DB]{Client: client},
		SessionStore:    NewSessionDAO(client),
	}

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
	SessionStore *SessionDAO
}

func (pms *PostgresMemoryStore) OnStart(
	ctx context.Context,
	appState *models.AppState,
) error {
	err := CreateSchema(ctx, appState, pms.Client)
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
	return pms.SessionStore.Get(ctx, sessionID)
}

// CreateSession creates or updates a Session for a given sessionID.
func (pms *PostgresMemoryStore) CreateSession(
	ctx context.Context,
	_ *models.AppState,
	session *models.CreateSessionRequest,
) (*models.Session, error) {
	return pms.SessionStore.Create(ctx, session)
}

// UpdateSession creates or updates a Session for a given sessionID.
func (pms *PostgresMemoryStore) UpdateSession(
	ctx context.Context,
	_ *models.AppState,
	session *models.UpdateSessionRequest,
) (*models.Session, error) {
	return pms.SessionStore.Update(ctx, session, false)
}

// DeleteSession deletes a session from the memory store. This is a soft Delete.
func (pms *PostgresMemoryStore) DeleteSession(ctx context.Context, sessionID string) error {
	return pms.SessionStore.Delete(ctx, sessionID)
}

// ListSessions returns a list of all Sessions.
func (pms *PostgresMemoryStore) ListSessions(
	ctx context.Context,
	_ *models.AppState,
	cursor int64,
	limit int,
) ([]*models.Session, error) {
	return pms.SessionStore.ListAll(ctx, cursor, limit)
}

// ListSessionsOrdered returns an ordered list of all Sessions, paginated by pageNumber and pageSize.
// orderedBy is the column to order by. asc is a boolean indicating whether to order ascending or descending.
func (pms *PostgresMemoryStore) ListSessionsOrdered(
	ctx context.Context,
	_ *models.AppState,
	pageNumber int,
	pageSize int,
	orderedBy string,
	asc bool,
) (*models.SessionListResponse, error) {
	return pms.SessionStore.ListAllOrdered(ctx, pageNumber, pageSize, orderedBy, asc)
}

// GetMemory returns memory for a given sessionID.
// If config.Type is SimpleMemoryType, returns the most recent Summary and a list of messages.
// If config.Type is PerpetualMemoryType, returns the last X messages, optionally the most recent summary
// and a list of summaries semantically similar to the most recent messages.
func (pms *PostgresMemoryStore) GetMemory(
	ctx context.Context,
	appState *models.AppState,
	config *models.MemoryConfig,
) (*models.Memory, error) {
	if appState == nil {
		return nil, errors.New("nil appState received")
	}
	if config == nil {
		return nil, errors.New("nil config received")
	}

	switch config.Type {
	case models.SimpleMemoryType:
		return getSimpleMemory(ctx, pms.Client, appState, config)
	case models.PerpetualMemoryType:
		return getPerpetualMemory(ctx, pms.Client, appState, config)
	default:
		return nil, errors.New("invalid memory type")
	}
}

// GetMessageList retrieves a list of messages for a given sessionID. Paginated by cursor and limit.
func (pms *PostgresMemoryStore) GetMessageList(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	pageNumber int,
	pageSize int,
) (*models.MessageListResponse, error) {
	if appState == nil {
		return nil, store.NewStorageError("nil appState received", nil)
	}

	messages, err := getMessageList(ctx, pms.Client, sessionID, pageNumber, pageSize)
	if err != nil {
		return nil, store.NewStorageError("failed to get messages", err)
	}

	return messages, nil
}

func (pms *PostgresMemoryStore) GetMessagesByUUID(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	uuids []uuid.UUID,
) ([]models.Message, error) {
	messages, err := getMessagesByUUID(ctx, pms.Client, sessionID, uuids)
	if err != nil {
		return nil, store.NewStorageError("failed to get messages", err)
	}

	return messages, nil
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

func (pms *PostgresMemoryStore) GetSummaryByUUID(ctx context.Context,
	appState *models.AppState,
	sessionID string,
	uuid uuid.UUID) (*models.Summary, error) {
	summary, err := getSummaryByUUID(ctx, appState, pms.Client, sessionID, uuid)
	if err != nil {
		return nil, store.NewStorageError("failed to get summary", err)
	}

	return summary, nil
}

func (pms *PostgresMemoryStore) GetSummaryList(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	pageNumber int,
	pageSize int,
) (*models.SummaryListResponse, error) {
	if appState == nil {
		return nil, store.NewStorageError("nil appState received", nil)
	}

	summaries, err := getSummaryList(ctx, pms.Client, sessionID, pageNumber, pageSize)
	if err != nil {
		return nil, store.NewStorageError("failed to get summaries", err)
	}

	return summaries, nil
}

func (pms *PostgresMemoryStore) PutSummary(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	summary *models.Summary,
) error {
	retSummary, err := putSummary(ctx, pms.Client, sessionID, summary)
	if err != nil {
		return store.NewStorageError("failed to Create summary", err)
	}

	// Publish a message to the message summary embeddings topic
	task := models.MessageSummaryTask{
		UUID: retSummary.UUID,
	}
	err = appState.TaskPublisher.Publish(
		models.MessageSummaryEmbedderTopic,
		map[string]string{
			"session_id": sessionID,
		},
		task,
	)
	if err != nil {
		return fmt.Errorf("MessageSummaryTask publish failed: %w", err)
	}

	err = appState.TaskPublisher.Publish(
		models.MessageSummaryNERTopic,
		map[string]string{
			"session_id": sessionID,
		},
		task,
	)
	if err != nil {
		return fmt.Errorf("MessageSummaryTask publish failed: %w", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) UpdateSummaryMetadata(ctx context.Context,
	_ *models.AppState,
	summary *models.Summary) error {
	_, err := updateSummaryMetadata(ctx, pms.Client, summary)
	if err != nil {
		return fmt.Errorf("failed to update summary metadata %w", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) PutSummaryEmbedding(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	embedding *models.TextData,
) error {
	err := putSummaryEmbedding(ctx, pms.Client, sessionID, embedding)
	if err != nil {
		return store.NewStorageError("failed to Create summary embedding", err)
	}

	return nil
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

	// If we are skipping pushing new messages to the message router, return early
	if skipNotify {
		return nil
	}

	mt := make([]models.MessageTask, len(messageResult))
	for i, message := range messageResult {
		mt[i] = models.MessageTask{UUID: message.UUID}
	}

	// Send new messages to the message router
	err = appState.TaskPublisher.PublishMessage(
		map[string]string{"session_id": sessionID},
		mt,
	)
	if err != nil {
		return store.NewStorageError("failed to publish new messages", err)
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
	searchResults, err := searchMemory(ctx, appState, pms.Client, sessionID, query, limit)
	return searchResults, err
}

func (pms *PostgresMemoryStore) Close() error {
	if pms.Client != nil {
		return pms.Client.Close()
	}
	return nil
}

func (pms *PostgresMemoryStore) PutMessageEmbeddings(ctx context.Context,
	_ *models.AppState,
	sessionID string,
	embeddings []models.TextData,
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

func (pms *PostgresMemoryStore) GetMessageEmbeddings(ctx context.Context,
	_ *models.AppState,
	sessionID string,
) ([]models.TextData, error) {
	embeddings, err := getMessageEmbeddings(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, store.NewStorageError("GetMessageEmbeddings failed to get embeddings", err)
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
