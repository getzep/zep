package memorystore

import (
	"context"
	"database/sql"

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
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}

	// We're not going to run this in a transaction as we don't necessarily
	// need to roll back the session creation if the message metadata upsert fails.
	session := PgSession{SessionID: sessionID}
	_, err := db.NewInsert().
		Model(&session).
		Column("session_id").
		On("CONFLICT (session_id) DO UPDATE"). // we'll do an upsert
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to put session", err)
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
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, NewStorageError("failed to begin transaction", err)
	}
	defer rollbackOnError(tx)

	err = acquireAdvisoryLock(ctx, tx, sessionID)
	if err != nil {
		return nil, NewStorageError("failed to acquire advisory lock", err)
	}

	dbSession := &PgSession{}
	_, err = db.NewSelect().
		Model(dbSession).
		Where("session_id = ?", sessionID).
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to get session", err)
	}

	// merge the existing metadata with the new metadata
	dbMetadata := dbSession.Metadata
	if dbMetadata == nil {
		dbMetadata = map[string]interface{}{}
	}
	err = storeMetadataByPath(dbMetadata, nil, metadata)
	if err != nil {
		return nil, NewStorageError("failed to store metadata", err)
	}

	// put the session metadata
	_, err = db.NewUpdate().
		Model(&dbSession).
		Set("metadata = ?", dbSession.Metadata).
		Where("session_id = ?", sessionID).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to update session metadata", err)
	}

	err = tx.Commit()
	if err != nil {
		return nil, NewStorageError("failed to commit update session metadata transaction", err)
	}

	session := &models.Session{}
	err = copier.Copy(session, dbSession)
	if err != nil {
		return nil, NewStorageError("Unable to copy session", err)
	}

	return session, nil
}

//// putSessionMetadata updates the metadata for a session. The metadata map is merged
//// with the existing metadata map, creating keys and values if they don't exist.
//func putSessionMetadata(ctx context.Context,
//	db *bun.DB,
//	sessionID string,
//	metadata map[string]interface{}) (*models.Session, error) {
//	flatMetadata := map[string]interface{}{}
//	internal.FlattenMap("", metadata, flatMetadata)
//
//	tx, err := db.BeginTx(ctx, nil)
//	if err != nil {
//		return nil, NewStorageError("failed to begin transaction", err)
//	}
//	defer rollbackOnError(tx)
//
//	err = acquireAdvisoryLock(ctx, tx, sessionID)
//	if err != nil {
//		return nil, NewStorageError("failed to acquire advisory lock", err)
//	}
//	for k, v := range flatMetadata {
//		var pathSlice []string
//		for _, elem := range strings.Split(k, ".") {
//			pathSlice = append(pathSlice, fmt.Sprintf("\"%s\"", elem))
//		}
//		path := fmt.Sprintf("{%s}", strings.Join(pathSlice, ","))
//
//		_, err = tx.ExecContext(
//			ctx,
//			"UPDATE session SET metadata = jsonb_set(metadata, ?, to_jsonb(?::text), true) WHERE session_id = ?",
//			path,
//			fmt.Sprintf("%v", v),
//			sessionID,
//		)
//		if err != nil {
//			return nil, NewStorageError("failed to update session metadata", err)
//		}
//	}
//	err = tx.Commit()
//	if err != nil {
//		return nil, NewStorageError("failed to commit update session metadata transaction", err)
//	}
//
//	return getSession(ctx, db, sessionID)
//}

// getSession retrieves a session from the memory store.
func getSession(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
) (*models.Session, error) {
	session := PgSession{}
	err := db.NewSelect().Model(&session).Where("session_id = ?", sessionID).Scan(ctx)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, NewStorageError("failed to get session", err)
	}

	retSession := models.Session{}
	err = copier.Copy(&retSession, &session)
	if err != nil {
		return nil, NewStorageError("failed to copy session", err)
	}

	return &retSession, nil
}
