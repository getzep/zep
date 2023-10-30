package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/driver/pgdriver"
)

var _ models.SessionManager = &SessionDAO{}

// SessionDAO implements the SessionManager interface.
type SessionDAO struct {
	db *bun.DB
}

// NewSessionDAO is a constructor for the SessionDAO struct.
// It takes a pointer to a bun.DB instance and returns a pointer to a new SessionDAO instance.
func NewSessionDAO(db *bun.DB) *SessionDAO {
	return &SessionDAO{
		db: db,
	}
}

// Create creates a new session in the database.
// It takes a context and a pointer to a CreateSessionRequest struct.
// It returns a pointer to the created Session struct or an error if the creation fails.
func (dao *SessionDAO) Create(
	ctx context.Context,
	session *models.CreateSessionRequest,
) (*models.Session, error) {
	if session.SessionID == "" {
		return nil, errors.New("sessionID cannot be empty")
	}
	sessionDB := SessionSchema{
		SessionID: session.SessionID,
		UserID:    session.UserID,
		Metadata:  session.Metadata,
	}
	_, err := dao.db.NewInsert().
		Model(&sessionDB).
		Returning("*").
		Exec(ctx)
	if err != nil {
		if err, ok := err.(pgdriver.Error); ok && err.IntegrityViolation() {
			if strings.Contains(err.Error(), "user") {
				return nil, models.NewBadRequestError(
					"user does not exist with user_id: " + *session.UserID,
				)
			}
			return nil, models.NewBadRequestError(
				"session already exists with session_id: " + session.SessionID,
			)
		}
		return nil, fmt.Errorf("failed to create session: %w", err)
	}

	return &models.Session{
		UUID:      sessionDB.UUID,
		ID:        sessionDB.ID,
		CreatedAt: sessionDB.CreatedAt,
		UpdatedAt: sessionDB.UpdatedAt,
		SessionID: sessionDB.SessionID,
		Metadata:  sessionDB.Metadata,
		UserID:    sessionDB.UserID,
	}, nil
}

// Get retrieves a session from the database by its sessionID.
// It takes a context and a session ID string.
// It returns a pointer to the retrieved Session struct or an error if the retrieval fails.
func (dao *SessionDAO) Get(ctx context.Context, sessionID string) (*models.Session, error) {
	session := SessionSchema{}
	err := dao.db.NewSelect().
		Model(&session).
		Where("session_id = ?", sessionID).
		Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, models.NewNotFoundError("session " + sessionID)
		}
		return nil, fmt.Errorf("failed to get session: %w", err)
	}

	retSession := models.Session{
		UUID:      session.UUID,
		ID:        session.ID,
		CreatedAt: session.CreatedAt,
		UpdatedAt: session.UpdatedAt,
		SessionID: session.SessionID,
		Metadata:  session.Metadata,
		UserID:    session.UserID,
	}
	return &retSession, nil
}

// Update updates a session in the database.
// It takes a context, a pointer to a UpdateSessionRequest struct, and a boolean indicating whether the caller is privileged.
// It returns an error if the update fails.
// Note: Update will update soft-deleted sessions and undelete them. Messages and message embeddings are not undeleted.
func (dao *SessionDAO) Update(
	ctx context.Context,
	session *models.UpdateSessionRequest,
	isPrivileged bool,
) (*models.Session, error) {
	if session.SessionID == "" {
		return nil, errors.New("sessionID cannot be empty")
	}

	// if metadata is null, we can keep this a cheap operation
	if session.Metadata == nil {
		return dao.updateSession(ctx, session)
	}

	// Acquire a lock for this SessionID. This is to prevent concurrent updates
	// to the session metadata.
	lockID, err := acquireAdvisoryLock(ctx, dao.db, session.SessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to acquire advisory lock: %w", err)
	}
	defer func(ctx context.Context, db bun.IDB, lockID uint64) {
		err := releaseAdvisoryLock(ctx, db, lockID)
		if err != nil {
			log.Errorf("failed to release advisory lock: %v", err)
		}
	}(ctx, dao.db, lockID)

	mergedMetadata, err := mergeMetadata(
		ctx,
		dao.db,
		"session_id",
		session.SessionID,
		"session",
		session.Metadata,
		isPrivileged,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to merge session metadata: %w", err)
	}

	session = &models.UpdateSessionRequest{
		SessionID: session.SessionID,
		Metadata:  mergedMetadata,
	}
	return dao.updateSession(ctx, session)
}

// updateSession updates a session in the database. It expects the metadata to be merged.
func (dao *SessionDAO) updateSession(
	ctx context.Context,
	session *models.UpdateSessionRequest,
) (*models.Session, error) {
	sessionDB := SessionSchema{
		SessionID: session.SessionID,
		Metadata:  session.Metadata,
		DeletedAt: time.Time{}, // Intentionally overwrite soft-delete with zero value
	}
	var columns = []string{"deleted_at", "updated_at"}
	if session.Metadata != nil {
		columns = append(columns, "metadata")
	}
	r, err := dao.db.NewUpdate().
		Model(&sessionDB).
		// intentionally overwrite the deleted_at field, undeleting the session
		// if the session exists and is deleted
		Column(columns...).
		// use WhereAllWithDeleted to update soft-deleted sessions
		WhereAllWithDeleted().
		Where("session_id = ?", session.SessionID).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to update session %w", err)
	}
	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rowsAffected == 0 {
		return nil, models.NewNotFoundError("session " + session.SessionID)
	}

	returnedSession := models.Session{
		UUID:      sessionDB.UUID,
		ID:        sessionDB.ID,
		CreatedAt: sessionDB.CreatedAt,
		UpdatedAt: sessionDB.UpdatedAt,
		SessionID: sessionDB.SessionID,
		Metadata:  sessionDB.Metadata,
		UserID:    sessionDB.UserID,
	}

	return &returnedSession, nil
}

// Delete soft-deletes a session from the database by its sessionID.
// It also soft-deletes all messages, message embeddings, and summaries associated with the session.
func (dao *SessionDAO) Delete(ctx context.Context, sessionID string) error {
	dbSession := &SessionSchema{}

	tx, err := dao.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	r, err := tx.NewDelete().
		Model(dbSession).
		Where("session_id = ?", sessionID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete session: %w", err)
	}

	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rowsAffected == 0 {
		return models.NewNotFoundError("session " + sessionID)
	}

	// delete all messages, message embeddings, and summaries associated with the session
	for _, schema := range messageTableList {
		if _, ok := schema.(*SessionSchema); ok {
			continue
		}
		log.Debugf("deleting session %s from schema %T", sessionID, schema)
		_, err := tx.NewDelete().
			Model(schema).
			Where("session_id = ?", sessionID).
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error deleting rows from %T: %w", schema, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}

// ListAll retrieves all sessions from the database.
// It takes a context, a cursor int64, and a limit int.
// It returns a slice of pointers to Session structs or an error if the retrieval fails.
func (dao *SessionDAO) ListAll(
	ctx context.Context,
	cursor int64,
	limit int,
) ([]*models.Session, error) {
	var sessions []SessionSchema
	err := dao.db.NewSelect().
		Model(&sessions).
		Where("id > ?", cursor).
		Order("id ASC").
		Limit(limit).
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to list sessions: %w", err)
	}

	retSessions := sessionSchemaToSession(sessions)

	return retSessions, nil
}

func (dao *SessionDAO) ListAllOrdered(
	ctx context.Context,
	pageNumber int,
	pageSize int,
	orderBy string,
	asc bool,
) (*models.SessionListResponse, error) {
	var totalCount int
	var wg sync.WaitGroup
	var mu sync.Mutex
	var firstErr error
	var sessions []SessionSchema

	if orderBy == "" {
		orderBy = "id"
	}

	wg.Add(1)
	go func() {
		defer wg.Done()
		err := dao.db.NewSelect().
			Model(&sessions).
			Order(fmt.Sprintf("%s %s", orderBy, getAscDesc(asc))).
			Limit(pageSize).
			Offset((pageNumber - 1) * pageSize).
			Scan(ctx)

		mu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		mu.Unlock()
	}()

	wg.Add(1)
	go func() {
		defer wg.Done()
		var err error
		totalCount, err = dao.db.NewSelect().
			Model((*SessionSchema)(nil)).
			Count(ctx)

		mu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		mu.Unlock()
	}()

	wg.Wait()

	if firstErr != nil {
		return nil, fmt.Errorf("failed to list sessions: %w", firstErr)
	}

	retSessions := sessionSchemaToSession(sessions)

	return &models.SessionListResponse{
		Sessions:   retSessions,
		TotalCount: totalCount,
		RowCount:   len(retSessions),
	}, nil
}

func sessionSchemaToSession(sessions []SessionSchema) []*models.Session {
	retSessions := make([]*models.Session, len(sessions))
	for i := range sessions {
		retSessions[i] = &models.Session{
			UUID:      sessions[i].UUID,
			ID:        sessions[i].ID,
			CreatedAt: sessions[i].CreatedAt,
			UpdatedAt: sessions[i].UpdatedAt,
			SessionID: sessions[i].SessionID,
			Metadata:  sessions[i].Metadata,
			UserID:    sessions[i].UserID,
		}
	}
	return retSessions
}
