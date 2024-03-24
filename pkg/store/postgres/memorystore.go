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
		appState:        appState,
	}

	err := pms.OnStart(context.Background())
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
	appState     *models.AppState
}

func (pms *PostgresMemoryStore) OnStart(
	ctx context.Context,
) error {
	err := CreateSchema(ctx, pms.appState, pms.Client)
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
	sessionID string,
) (*models.Session, error) {
	return pms.SessionStore.Get(ctx, sessionID)
}

// CreateSession creates or updates a Session for a given sessionID.
func (pms *PostgresMemoryStore) CreateSession(
	ctx context.Context,
	session *models.CreateSessionRequest,
) (*models.Session, error) {
	return pms.SessionStore.Create(ctx, session)
}

// UpdateSession creates or updates a Session for a given sessionID.
func (pms *PostgresMemoryStore) UpdateSession(
	ctx context.Context,
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
	cursor int64,
	limit int,
) ([]*models.Session, error) {
	return pms.SessionStore.ListAll(ctx, cursor, limit)
}

// ListSessionsOrdered returns an ordered list of all Sessions, paginated by pageNumber and pageSize.
// orderedBy is the column to order by. asc is a boolean indicating whether to order ascending or descending.
func (pms *PostgresMemoryStore) ListSessionsOrdered(
	ctx context.Context,
	pageNumber int,
	pageSize int,
	orderedBy string,
	asc bool,
) (*models.SessionListResponse, error) {
	return pms.SessionStore.ListAllOrdered(ctx, pageNumber, pageSize, orderedBy, asc)
}

// GetMemory returns the most recent Summary and a list of messages for a given sessionID.
// GetMemory returns:
//   - the most recent Summary, if one exists
//   - the lastNMessages messages, if lastNMessages > 0
//   - all messages since the last SummaryPoint, if lastNMessages == 0
//   - if no Summary (and no SummaryPoint) exists and lastNMessages == 0, returns
//     all undeleted messages up to the configured message window
func (pms *PostgresMemoryStore) GetMemory(
	ctx context.Context,
	sessionID string,
	lastNMessages int,
) (*models.Memory, error) {
	if lastNMessages < 0 {
		return nil, errors.New("cannot specify negative lastNMessages")
	}

	memoryDAO, err := NewMemoryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create memoryDAO: %w", err)
	}

	return memoryDAO.Get(ctx, lastNMessages)
}

func (pms *PostgresMemoryStore) PutMemory(
	ctx context.Context,
	sessionID string,
	memoryMessages *models.Memory,
	skipNotify bool,
) error {
	memoryDAO, err := NewMemoryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return fmt.Errorf("failed to create memoryDAO: %w", err)
	}

	return memoryDAO.Create(ctx, memoryMessages, skipNotify)
}

// GetMessageList retrieves a list of messages for a given sessionID. Paginated by cursor and limit.
func (pms *PostgresMemoryStore) GetMessageList(
	ctx context.Context,
	sessionID string,
	pageNumber int,
	pageSize int,
) (*models.MessageListResponse, error) {
	messageDAO, err := NewMessageDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create messageDAO: %w", err)
	}

	return messageDAO.GetListBySession(ctx, pageNumber, pageSize)
}

func (pms *PostgresMemoryStore) GetMessagesByUUID(
	ctx context.Context,
	sessionID string,
	uuids []uuid.UUID,
) ([]models.Message, error) {
	messageDAO, err := NewMessageDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create messageDAO: %w", err)
	}

	return messageDAO.GetListByUUID(ctx, uuids)
}

func (pms *PostgresMemoryStore) GetSummary(
	ctx context.Context,
	sessionID string,
) (*models.Summary, error) {
	summaryDAO, err := NewSummaryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create summaryDAO: %w", err)
	}

	return summaryDAO.Get(ctx)
}

func (pms *PostgresMemoryStore) GetSummaryByUUID(
	ctx context.Context,
	sessionID string,
	uuid uuid.UUID) (*models.Summary, error) {
	summaryDAO, err := NewSummaryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create summaryDAO: %w", err)
	}

	return summaryDAO.GetByUUID(ctx, uuid)
}

func (pms *PostgresMemoryStore) GetSummaryList(
	ctx context.Context,
	sessionID string,
	pageNumber int,
	pageSize int,
) (*models.SummaryListResponse, error) {
	summaryDAO, err := NewSummaryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create summaryDAO: %w", err)
	}

	return summaryDAO.GetList(ctx, pageNumber, pageSize)
}

func (pms *PostgresMemoryStore) CreateSummary(
	ctx context.Context,
	sessionID string,
	summary *models.Summary,
) error {
	summaryDAO, err := NewSummaryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return fmt.Errorf("failed to create summaryDAO: %w", err)
	}

	retSummary, err := summaryDAO.Create(ctx, summary)
	if err != nil {
		return store.NewStorageError("failed to create summary", err)
	}

	// Publish a message to the message summary embeddings topic
	task := models.MessageSummaryTask{
		UUID: retSummary.UUID,
	}
	err = pms.appState.TaskPublisher.Publish(
		models.MessageSummaryEmbedderTopic,
		map[string]string{
			"session_id": sessionID,
		},
		task,
	)
	if err != nil {
		return fmt.Errorf("MessageSummaryTask publish failed: %w", err)
	}

	err = pms.appState.TaskPublisher.Publish(
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

func (pms *PostgresMemoryStore) UpdateSummary(ctx context.Context,
	sessionID string,
	summary *models.Summary,
	metadataOnly bool,
) error {
	summaryDAO, err := NewSummaryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return fmt.Errorf("failed to create summaryDAO: %w", err)
	}

	_, err = summaryDAO.Update(ctx, summary, metadataOnly)
	if err != nil {
		return fmt.Errorf("failed to update summary metadata %w", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) PutSummaryEmbedding(
	ctx context.Context,
	sessionID string,
	embedding *models.TextData,
) error {
	summaryDAO, err := NewSummaryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return fmt.Errorf("failed to create summaryDAO: %w", err)
	}

	return summaryDAO.PutEmbedding(ctx, embedding)
}

func (pms *PostgresMemoryStore) UpdateMessages(
	ctx context.Context,
	sessionID string,
	messages []models.Message,
	isPrivileged bool,
	includeContent bool,
) error {
	messageDAO, err := NewMessageDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return fmt.Errorf("failed to create messageDAO: %w", err)
	}

	return messageDAO.UpdateMany(ctx, messages, includeContent, isPrivileged)
}

func (pms *PostgresMemoryStore) SearchMemory(
	ctx context.Context,
	sessionID string,
	query *models.MemorySearchPayload,
	limit int,
) ([]models.MemorySearchResult, error) {
	memoryDAO, err := NewMemoryDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create memoryDAO: %w", err)
	}
	return memoryDAO.Search(ctx, query, limit)
}

func (pms *PostgresMemoryStore) Close() error {
	if pms.Client != nil {
		return pms.Client.Close()
	}
	return nil
}

func (pms *PostgresMemoryStore) CreateMessageEmbeddings(ctx context.Context,
	sessionID string,
	embeddings []models.TextData,
) error {
	messageDAO, err := NewMessageDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return fmt.Errorf("failed to create messageDAO: %w", err)
	}

	return messageDAO.CreateEmbeddings(ctx, embeddings)
}

func (pms *PostgresMemoryStore) GetMessageEmbeddings(ctx context.Context,
	sessionID string,
) ([]models.TextData, error) {
	messageDAO, err := NewMessageDAO(pms.Client, pms.appState, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to create messageDAO: %w", err)
	}

	return messageDAO.GetEmbeddingListBySession(ctx)
}

func (pms *PostgresMemoryStore) PurgeDeleted(ctx context.Context) error {
	err := purgeDeleted(ctx, pms.Client)
	if err != nil {
		return store.NewStorageError("failed to purge deleted", err)
	}

	return nil
}

func generateLockID(key string) uint64 {
	hasher := sha256.New()
	hasher.Write([]byte(key))
	hash := hasher.Sum(nil)
	return binary.BigEndian.Uint64(hash[:8])
}

// tryAcquireAdvisoryLock attempts to acquire a PostgreSQL advisory lock using pg_try_advisory_lock.
// This function will fail if it's unable to immediately acquire a lock.
// Accepts a bun.IDB, which can be either a *bun.DB or *bun.Tx.
// Returns the lock ID and a boolean indicating if the lock was successfully acquired.
func tryAcquireAdvisoryLock(ctx context.Context, db bun.IDB, key string) (uint64, error) {
	lockID := generateLockID(key)

	var acquired bool
	if err := db.QueryRowContext(ctx, "SELECT pg_try_advisory_lock(?)", lockID).Scan(&acquired); err != nil {
		return 0, fmt.Errorf("tryAcquireAdvisoryLock: %w", err)
	}
	if !acquired {
		return 0, models.NewAdvisoryLockError(fmt.Errorf("failed to acquire advisory lock for %s", key))
	}
	return lockID, nil
}

// acquireAdvisoryLock acquires a PostgreSQL advisory lock for the given key.
func acquireAdvisoryLock(ctx context.Context, db bun.IDB, key string) (uint64, error) {
	lockID := generateLockID(key)

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
