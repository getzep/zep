package postgres

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/getzep/zep/pkg/memorystore"

	"dario.cat/mergo"

	"github.com/getzep/zep/pkg/models"
	"github.com/jinzhu/copier"
	"github.com/uptrace/bun"
)

// putSession stores a new session or updates an existing session with new metadata.
func putSession(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	metadata map[string]interface{},
	isPrivileged bool,
) (*models.Session, error) {
	if sessionID == "" {
		return nil, memorystore.NewStorageError("sessionID cannot be empty", nil)
	}

	// We're not going to run this in a transaction as we don't necessarily
	// need to roll back the session creation if the message metadata upsert fails.
	session := SessionSchema{SessionID: sessionID}
	_, err := db.NewInsert().
		Model(&session).
		Column("session_id").
		On("CONFLICT (session_id) DO UPDATE"). // we'll do an upsert
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to put session", err)
	}

	// If the session is deleted, return an error
	if !session.DeletedAt.IsZero() {
		return nil, memorystore.NewStorageError(
			fmt.Sprintf("session %s is deleted", sessionID),
			nil,
		)
	}

	// remove the top-level `system` key from the metadata if the caller is not privileged
	if !isPrivileged {
		delete(metadata, "system")
	}

	// update the session metadata and return the session
	return putSessionMetadata(ctx, db, sessionID, metadata)
}

// putSessionMetadata updates the metadata for a session. The metadata map is merged
// with the existing metadata map, creating keys and values if they don't exist.
func putSessionMetadata(ctx context.Context,
	db *bun.DB,
	sessionID string,
	metadata map[string]interface{}) (*models.Session, error) {
	// Acquire a lock for this SessionID. This is to prevent concurrent updates
	// to the session metadata.
	lockID, err := acquireAdvisoryLock(ctx, db, sessionID)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to acquire advisory lock", err)
	}
	defer func(ctx context.Context, db bun.IDB, lockID uint64) {
		err := releaseAdvisoryLock(ctx, db, lockID)
		if err != nil {
			log.Error(ctx, "failed to release advisory lock", err)
		}
	}(ctx, db, lockID)

	dbSession := &SessionSchema{}
	err = db.NewSelect().
		Model(dbSession).
		Where("session_id = ?", sessionID).
		Scan(ctx)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to get session", err)
	}

	// merge the existing metadata with the new metadata
	dbMetadata := dbSession.Metadata
	if err := mergo.Merge(&dbMetadata, metadata, mergo.WithOverride); err != nil {
		return nil, memorystore.NewStorageError("failed to merge metadata", err)
	}

	// put the session metadata, returning the updated session
	_, err = db.NewUpdate().
		Model(dbSession).
		Set("metadata = ?", dbMetadata).
		Where("session_id = ?", sessionID).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to update session metadata", err)
	}

	session := &models.Session{}
	err = copier.Copy(session, dbSession)
	if err != nil {
		return nil, memorystore.NewStorageError("Unable to copy session", err)
	}

	return session, nil
}

// getSession retrieves a session from the memory store.
func getSession(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
) (*models.Session, error) {
	session := SessionSchema{}
	err := db.NewSelect().Model(&session).Where("session_id = ?", sessionID).Scan(ctx)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, memorystore.NewStorageError("failed to get session", err)
	}

	retSession := models.Session{}
	err = copier.Copy(&retSession, &session)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to copy session", err)
	}

	return &retSession, nil
}
