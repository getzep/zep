package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/driver/pgdriver" //nolint:typecheck // linter is confused in CE and thinks this is unused

	"github.com/getzep/zep/lib/enablement"
	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/telemetry"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

func NewSessionDAO(as *models.AppState, rs *models.RequestState) models.SessionStore {
	return &sessionDAO{
		as: as,
		rs: rs,
	}
}

type sessionDAO struct {
	as *models.AppState
	rs *models.RequestState
}

// Create creates a new session in the database.
// It takes a context and a pointer to a CreateSessionRequest struct.
// It returns a pointer to the created Session struct or an error if the creation fails.
func (dao *sessionDAO) Create(ctx context.Context, session *models.CreateSessionRequest) (*models.Session, error) {
	if dao.rs.ProjectUUID == uuid.Nil {
		return nil, errors.New("projectUUID cannot be nil")
	}
	if session.SessionID == "" {
		return nil, zerrors.NewBadRequestError("sessionID cannot be empty")
	}

	sessionDB := SessionSchema{
		SessionSchemaExt: sessionSchemaExt(session),
		SessionID:        session.SessionID,
		UserID:           session.UserID,
		Metadata:         session.Metadata,
		ProjectUUID:      dao.rs.ProjectUUID,
		BaseSchema:       NewBaseSchema(dao.rs.SchemaName, "sessions"),
	}
	_, err := dao.as.DB.NewInsert().
		Model(&sessionDB).
		ModelTableExpr("? as s", bun.Ident(sessionDB.GetTableName())).
		Returning("*").
		Exec(ctx)
	if err != nil {
		if err, ok := err.(pgdriver.Error); ok && err.IntegrityViolation() {
			if strings.Contains(err.Error(), "user") {
				return nil, zerrors.NewBadRequestError(
					"user does not exist with user_id: " + *session.UserID,
				)
			}
			return nil, zerrors.NewBadRequestError(
				"session already exists with session_id: " + session.SessionID,
			)
		}
		return nil, fmt.Errorf("failed to create session: %w", err)
	}

	telemetry.I().TrackEvent(dao.rs, telemetry.Event_CreateSession)
	enablement.I().TrackEvent(enablement.Event_CreateSession, dao.rs)

	if session.UserID != nil {
		err = dao._postCreateSession(ctx, session.SessionID, *session.UserID)
		if err != nil {
			return nil, fmt.Errorf("failed to post create session: %w", err)
		}
	}

	resp := sessionSchemaToSession(sessionDB)
	return resp[0], nil
}

// Helper function. Gets a session by its sessionID. Allows user to include soft-deleted sessions.
func (dao *sessionDAO) getBySessionID(ctx context.Context, sessionID string, includeDeleted bool) (*SessionSchema, error) {
	session := SessionSchema{
		BaseSchema: NewBaseSchema(dao.rs.SchemaName, "sessions"),
	}

	// Expected to use session_id_project_uuid_deleted_at_idx. Do not change the order of the where clauses.
	query := dao.as.DB.NewSelect().
		Model(&session).
		ModelTableExpr("? as s", bun.Ident(session.GetTableName())).
		Where("session_id = ?", sessionID).
		Where("project_uuid = ?", dao.rs.ProjectUUID)

	if includeDeleted {
		query = query.WhereAllWithDeleted()
	}

	err := query.Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, zerrors.NewNotFoundError("session " + sessionID)
		}
		return nil, fmt.Errorf("sessionDAO getBySessionID failed to get session: %w", err)
	}
	return &session, err
}

// Update updates a session in the database.
// It takes a context, a pointer to a UpdateSessionRequest struct, and a boolean indicating whether the caller is privileged.
// It returns an error if the update fails.
// Note: Update will update soft-deleted sessions and undelete them. Messages and message embeddings are not undeleted.
func (dao *sessionDAO) Update(ctx context.Context, session *models.UpdateSessionRequest, isPrivileged bool) (*models.Session, error) {
	if session.SessionID == "" {
		return nil, zerrors.NewBadRequestError("sessionID cannot be empty")
	}

	currentSession, err := dao.getBySessionID(ctx, session.SessionID, true)
	if err != nil {
		return nil, fmt.Errorf("sessionDAO Update failed to get session: %w", err)
	}

	// Check if the session has ended
	if currentSession.EndedAt != nil {
		return nil, zerrors.NewSessionEndedError("session has ended")
	}

	// if metadata is null or {}, we can keep this a cheap operation
	if len(session.Metadata) == 0 {
		return dao.updateSession(ctx, session)
	}

	// Acquire a lock for this SessionID. This is to prevent concurrent updates
	// to the session metadata.
	lockID, err := safelyAcquireMetadataLock(ctx, dao.as.DB, session.SessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to acquire advisory lock: %w", zerrors.ErrLockAcquisitionFailed)
	}

	defer func(ctx context.Context, db bun.IDB, lockID uint64) {
		err := releaseAdvisoryLock(ctx, db, lockID)
		if err != nil {
			logger.Error("failed to release advisory lock", "error", err)
		}
	}(ctx, dao.as.DB, lockID)

	mergeData := mergeMetadataRequest{
		entityField: "session_id",
		entityID:    session.SessionID,
		table:       "sessions",
		metadata:    session.Metadata,
	}

	mergedMetadata, err := mergeMetadata(
		ctx,
		dao.as.DB,
		dao.rs.SchemaName,
		mergeData,
		isPrivileged,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to merge session metadata: %w", err)
	}

	session.Metadata = mergedMetadata

	return dao.updateSession(ctx, session)
}

// updateSession updates a session in the database. It expects the metadata to be merged.
func (dao *sessionDAO) updateSession(ctx context.Context, session *models.UpdateSessionRequest) (*models.Session, error) {
	sessionDB, columns := dao.buildUpdate(ctx, session)

	r, err := dao.as.DB.NewUpdate().
		Model(&sessionDB).
		ModelTableExpr("? as s", bun.Ident(sessionDB.GetTableName())).
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
		return nil, zerrors.NewNotFoundError("session " + session.SessionID)
	}

	return sessionSchemaToSession(sessionDB)[0], nil
}

// Delete soft-deletes a session from the database by its sessionID.
// It also soft-deletes all messages, message embeddings, and summaries associated with the session.
func (dao *sessionDAO) Delete(ctx context.Context, sessionID string) error {
	dbSession := &SessionSchema{}

	tx, err := dao.as.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	_, err = tx.Exec("SET LOCAL search_path TO ?"+SearchPathSuffix, dao.rs.SchemaName)
	if err != nil {
		return fmt.Errorf("error setting schema: %w", err)
	}

	r, err := tx.NewDelete().Model(dbSession).Where("session_id = ?", sessionID).Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete session: %w", err)
	}

	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rowsAffected == 0 {
		return zerrors.NewNotFoundError("session " + sessionID)
	}

	err = dao.cleanup(ctx, sessionID, tx)
	if err != nil {
		return fmt.Errorf("failed to cleanup session: %w", err)
	}

	for _, schema := range messageTableList {
		if _, ok := schema.(*SessionSchema); ok {
			continue
		}

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

	err = dao._cleanupDeletedSession(ctx)
	if err != nil {
		return err
	}

	telemetry.I().TrackEvent(dao.rs, telemetry.Event_DeleteSession)

	return nil
}

// ListAll retrieves all sessions from the database.
// It takes a context, a cursor int64, and a limit int.
// It returns a slice of pointers to Session structs or an error if the retrieval fails.
func (dao *sessionDAO) ListAll(ctx context.Context, cursor int64, limit int) ([]*models.Session, error) {
	if dao.rs.ProjectUUID == uuid.Nil {
		return nil, errors.New("projectUUID cannot be nil")
	}

	var sessions []SessionSchema
	q := dao.as.DB.NewSelect().
		Model(&sessions).
		ModelTableExpr("?.sessions as s", bun.Ident(dao.rs.SchemaName)).
		Where("s.project_uuid = ?", dao.rs.ProjectUUID).
		Where("id > ?", cursor).
		Order("id ASC").
		Limit(limit)

	dao.sessionRelations(q)

	err := q.Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to list sessions: %w", err)
	}

	retSessions := sessionSchemaToSession(sessions...)

	return retSessions, nil
}

func (dao *sessionDAO) ListAllOrdered(ctx context.Context, pageNumber, pageSize int, orderBy string, asc bool) (*models.SessionListResponse, error) {
	if dao.rs.ProjectUUID == uuid.Nil {
		return nil, errors.New("projectUUID cannot be nil")
	}

	var (
		totalCount int
		wg         sync.WaitGroup
		mu         sync.Mutex
		firstErr   error
		sessions   []SessionSchema
	)

	if orderBy == "" {
		orderBy = "id"
	}

	direction := "DESC"
	if asc {
		direction = "ASC"
	}

	wg.Add(1)
	go func() {
		defer wg.Done()
		q := dao.as.DB.NewSelect().
			Model(&sessions).
			ModelTableExpr("?.sessions as s", bun.Ident(dao.rs.SchemaName)).
			Where("s.project_uuid = ?", dao.rs.ProjectUUID).
			Order(fmt.Sprintf("%s %s", orderBy, direction)).
			Limit(pageSize).
			Offset((pageNumber - 1) * pageSize)

		dao.sessionRelations(q)

		err := q.Scan(ctx)

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
		totalCount, err = dao.as.DB.NewSelect().
			Model((*SessionSchema)(nil)).
			ModelTableExpr("?.sessions as s", bun.Ident(dao.rs.SchemaName)).
			Where("s.project_uuid = ?", dao.rs.ProjectUUID).
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
	retSessions := sessionSchemaToSession(sessions...)

	return &models.SessionListResponse{
		Sessions:   retSessions,
		TotalCount: totalCount,
		RowCount:   len(retSessions),
	}, nil
}

func (dao *sessionDAO) _buildUpdate(ctx context.Context, session *models.UpdateSessionRequest) (SessionSchema, []string) {
	sessionDB := SessionSchema{
		SessionID:  session.SessionID,
		Metadata:   session.Metadata,
		DeletedAt:  time.Time{}, // Intentionally overwrite soft-delete with zero value
		BaseSchema: NewBaseSchema(dao.rs.SchemaName, "sessions"),
	}

	columns := []string{"deleted_at", "updated_at"}
	if session.Metadata != nil {
		columns = append(columns, "metadata")
	}

	return sessionDB, columns
}

func _sessionSchemaToSession(session SessionSchema) *models.Session {
	return &models.Session{
		SessionCommon: models.SessionCommon{
			UUID:      session.UUID,
			ID:        session.ID,
			CreatedAt: session.CreatedAt,
			UpdatedAt: session.UpdatedAt,
			SessionID: session.SessionID,
			Metadata:  session.Metadata,
			UserID:    session.UserID,
		},
	}
}
