package memorystore

import (
	"context"
	"database/sql"
	"strings"

	"github.com/uptrace/bun"

	"github.com/getzep/zep/pkg/models"
)

// putMessageMetadata stores a new or updates existing message metadata. Existing
// metadata is determined by message UUID. isPrivileged is used to determine if
// the caller is allowed to store metadata in the `system` top-level key.
// Unprivileged callers will have the `system` key removed from the metadata.
func putMessageMetadata(
	ctx context.Context,
	_ *models.AppState,
	db *bun.DB,
	sessionID string,
	messageMetaSet []models.MessageMetadata,
	isPrivileged bool,
) error {
	tx, err := db.BeginTx(ctx, &sql.TxOptions{})
	if err != nil {
		return NewStorageError("failed to begin transaction", err)
	}
	defer rollbackOnError(tx)

	// remove the top-level `system` key from the metadata if the caller is not privileged
	if !isPrivileged {
		for i := range messageMetaSet {
			delete(messageMetaSet[i].Metadata, "system")
		}
	}

	for i := range messageMetaSet {
		err := putMessageMetadataTx(ctx, tx, sessionID, &messageMetaSet[i])
		if err != nil {
			// defer will roll back the transaction
			return NewStorageError("failed to put message metadata", err)
		}
	}

	if err = tx.Commit(); err != nil {
		return NewStorageError("failed to commit transaction", err)
	}

	return nil
}

func putMessageMetadataTx(
	ctx context.Context,
	tx bun.Tx,
	sessionID string,
	messageMetadata *models.MessageMetadata,
) error {
	err := acquireAdvisoryLock(ctx, tx, sessionID+messageMetadata.UUID.String())
	if err != nil {
		return NewStorageError("failed to acquire advisory lock", err)
	}

	var msg PgMessageStore
	err = tx.NewSelect().Model(&msg).
		Column("metadata").
		Where("session_id = ? AND uuid = ?", sessionID, messageMetadata.UUID).
		Scan(ctx)
	if err != nil {
		return NewStorageError("failed to retrieve existing metadata", err)
	}

	if msg.Metadata == nil {
		msg.Metadata = make(map[string]interface{})
	}

	storeMetadataByPath(
		msg.Metadata,
		strings.Split(messageMetadata.Key, "."),
		messageMetadata.Metadata,
	)

	msg.UUID = messageMetadata.UUID
	_, err = tx.NewUpdate().
		Model(&msg).
		Column("metadata").
		Where("session_id = ? AND uuid = ?", sessionID, messageMetadata.UUID).
		Exec(ctx)
	if err != nil {
		return NewStorageError("failed to update message metadata", err)
	}

	return nil
}

// findOrCreateMetadataMap finds or creates a map at the given key in the current map.
func findOrCreateMetadataMap(currentMap map[string]interface{}, key string) map[string]interface{} {
	if val, ok := currentMap[key]; ok && val != nil {
		return val.(map[string]interface{})
	}
	newMap := make(map[string]interface{})
	currentMap[key] = newMap
	return newMap
}

// storeMetadataByPath stores the metadata at the given key path in the value map.
func storeMetadataByPath(value map[string]interface{}, keyPath []string, metadata interface{}) {
	for _, key := range keyPath[:len(keyPath)-1] {
		value = findOrCreateMetadataMap(value, key)
	}
	value[keyPath[len(keyPath)-1]] = metadata
}
