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
) (*models.Session, error) {
	if sessionID == "" {
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}
	session := PgSession{SessionID: sessionID, Metadata: metadata}
	_, err := db.NewInsert().
		Model(&session).
		Column("session_id", "metadata").
		On("CONFLICT (session_id) DO UPDATE").
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to put session", err)
	}

	retSession := models.Session{}
	err = copier.Copy(&retSession, &session)
	if err != nil {
		return nil, NewStorageError("failed to copy session", err)
	}

	return &retSession, nil
}

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
