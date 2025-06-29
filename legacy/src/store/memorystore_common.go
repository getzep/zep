package store

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"errors"
	"fmt"
	"time"

	"github.com/failsafe-go/failsafe-go"
	"github.com/failsafe-go/failsafe-go/retrypolicy"
	"github.com/google/uuid"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

func NewMemoryStore(as *models.AppState, rs *models.RequestState) models.MemoryStore {
	return &memoryStore{
		as: as,
		rs: rs,
	}
}

type memoryStore struct {
	as *models.AppState
	rs *models.RequestState
}

func (ms *memoryStore) dao(sessionID string, lastNMessages int) *memoryDAO {
	return newMemoryDAO(ms.as, ms.rs, sessionID, lastNMessages)
}

func (ms *memoryStore) messages(sessionID string) *messageDAO {
	return newMessageDAO(ms.as, ms.rs, sessionID)
}

func (ms *memoryStore) GetSession(ctx context.Context, sessionID string) (*models.Session, error) {
	return ms.rs.Sessions.Get(ctx, sessionID)
}

func (ms *memoryStore) CreateSession(ctx context.Context, session *models.CreateSessionRequest) (*models.Session, error) {
	return ms.rs.Sessions.Create(ctx, session)
}

func (ms *memoryStore) UpdateSession(ctx context.Context, session *models.UpdateSessionRequest, isPrivileged bool) (*models.Session, error) {
	return ms.rs.Sessions.Update(ctx, session, isPrivileged)
}

func (ms *memoryStore) DeleteSession(ctx context.Context, sessionID string) error {
	return ms.rs.Sessions.Delete(ctx, sessionID)
}

func (ms *memoryStore) ListSessions(ctx context.Context, cursor int64, limit int) ([]*models.Session, error) {
	return ms.rs.Sessions.ListAll(ctx, cursor, limit)
}

func (ms *memoryStore) ListSessionsOrdered(
	ctx context.Context,
	pageNumber, pageSize int,
	orderedBy string,
	asc bool,
) (*models.SessionListResponse, error) {
	return ms.rs.Sessions.ListAllOrdered(ctx, pageNumber, pageSize, orderedBy, asc)
}

func (ms *memoryStore) GetMemory(
	ctx context.Context,
	sessionID string,
	lastNMessages int,
	opts ...models.MemoryFilterOption,
) (*models.Memory, error) {
	if lastNMessages < 0 {
		return nil, errors.New("cannot specify negative lastNMessages")
	}

	return ms.dao(sessionID, lastNMessages).Get(ctx, opts...)
}

func (ms *memoryStore) PutMemory(
	ctx context.Context,
	sessionID string,
	memoryMessages *models.Memory,
	skipProcessing bool,
) error {
	return ms.dao(sessionID, 0).Create(ctx, memoryMessages, skipProcessing)
}

func (ms *memoryStore) GetMessagesLastN(
	ctx context.Context,
	sessionID string,
	lastNMessages int,
	beforeUUID uuid.UUID,
) ([]models.Message, error) {
	if lastNMessages < 0 {
		return nil, errors.New("cannot specify negative lastNMessages")
	}

	return ms.messages(sessionID).GetLastN(ctx, lastNMessages, beforeUUID)
}

func (ms *memoryStore) GetMessageList(
	ctx context.Context,
	sessionID string,
	pageNumber, pageSize int,
) (*models.MessageListResponse, error) {
	return ms.messages(sessionID).GetListBySession(ctx, pageNumber, pageSize)
}

func (ms *memoryStore) GetMessagesByUUID(
	ctx context.Context,
	sessionID string,
	uuids []uuid.UUID,
) ([]models.Message, error) {
	return ms.messages(sessionID).GetListByUUID(ctx, uuids)
}

func (ms *memoryStore) PutMessages(ctx context.Context, sessionID string, messages []models.Message) ([]models.Message, error) {
	return ms.messages(sessionID).CreateMany(ctx, messages)
}

func (ms *memoryStore) UpdateMessages(
	ctx context.Context,
	sessionID string,
	messages []models.Message,
	isPrivileged, includeContent bool,
) error {
	return ms.messages(sessionID).UpdateMany(ctx, messages, includeContent, isPrivileged)
}

func (ms *memoryStore) SearchSessions(ctx context.Context, query *models.SessionSearchQuery, limit int) (*models.SessionSearchResponse, error) {
	return ms.dao("", 0).SearchSessions(ctx, query, limit)
}

func (ms *memoryStore) PurgeDeleted(ctx context.Context, schemaName string) error {
	err := purgeDeleted(ctx, ms.as.DB.DB, schemaName, ms.rs.ProjectUUID)
	if err != nil {
		return zerrors.NewStorageError("failed to purge deleted", err)
	}

	return nil
}

func generateLockID(key string) (uint64, error) {
	hasher := sha256.New()
	_, err := hasher.Write([]byte(key))
	if err != nil {
		return 0, fmt.Errorf("failed to hash key %w", err)
	}
	hash := hasher.Sum(nil)
	return binary.BigEndian.Uint64(hash[:8]), nil
}

// safelyAcquireMetadataLock attempts to safely acquire a PostgreSQL advisory lock for the given key using a default retry policy.
func safelyAcquireMetadataLock(ctx context.Context, db bun.IDB, key string) (uint64, error) {
	lockRetryPolicy := buildDefaultLockRetryPolicy()

	lockIDVal, err := failsafe.Get(
		func() (any, error) {
			return tryAcquireAdvisoryLock(ctx, db, key)
		}, lockRetryPolicy,
	)
	if err != nil {
		return 0, fmt.Errorf("failed to acquire advisory lock: %w", err)
	}

	lockID, ok := lockIDVal.(uint64)
	if !ok {
		return 0, fmt.Errorf("failed to acquire advisory lock: %w", zerrors.ErrLockAcquisitionFailed)
	}

	return lockID, nil
}

// tryAcquireAdvisoryLock attempts to acquire a PostgreSQL advisory lock using pg_try_advisory_lock.
// This function will fail if it's unable to immediately acquire a lock.
// Accepts a bun.IDB, which can be either a *bun.DB or *bun.Tx.
// Returns the lock ID and a boolean indicating if the lock was successfully acquired.
func tryAcquireAdvisoryLock(ctx context.Context, db bun.IDB, key string) (uint64, error) {
	lockID, err := generateLockID(key)
	if err != nil {
		return 0, fmt.Errorf("failed to generate lock ID: %w", err)
	}

	var acquired bool
	if err := db.QueryRowContext(ctx, "SELECT pg_try_advisory_lock(?)", lockID).Scan(&acquired); err != nil {
		return 0, fmt.Errorf("tryAcquireAdvisoryLock: %w", err)
	}
	if !acquired {
		return 0, zerrors.NewAdvisoryLockError(fmt.Errorf("failed to acquire advisory lock for %s", key))
	}
	return lockID, nil
}

func buildDefaultLockRetryPolicy() retrypolicy.RetryPolicy[any] {
	return retrypolicy.Builder[any]().
		HandleErrors(zerrors.ErrLockAcquisitionFailed).
		WithBackoff(200*time.Millisecond, 30*time.Second).
		WithMaxRetries(15).
		Build()
}

// releaseAdvisoryLock releases a PostgreSQL advisory lock for the given key.
// Accepts a bun.IDB, which can be either a *bun.DB or *bun.Tx.
func releaseAdvisoryLock(ctx context.Context, db bun.IDB, lockID uint64) error {
	if _, err := db.ExecContext(ctx, "SELECT pg_advisory_unlock(?)", lockID); err != nil {
		return fmt.Errorf("failed to release advisory lock %w", err)
	}

	return nil
}

// rollbackOnError rolls back the transaction if an error is encountered.
// If the error is sql.ErrTxDone, the transaction has already been committed or rolled back
// and we ignore the error.
func rollbackOnError(tx bun.Tx) {
	if rollBackErr := tx.Rollback(); rollBackErr != nil && !errors.Is(rollBackErr, sql.ErrTxDone) {
		logger.Error("failed to rollback transaction", "error", rollBackErr)
	}
}
