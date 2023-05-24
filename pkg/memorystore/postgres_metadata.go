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
// Can be enrolled in an existing transaction by passing a bun.Tx as db.
func putMessageMetadata(
	ctx context.Context,
	db bun.IDB,
	sessionID string,
	messageMetaSet []models.MessageMetadata,
	isPrivileged bool,
) error {
	var tx bun.Tx
	var err error

	// remove the top-level `system` key from the metadata if the caller is not privileged
	if !isPrivileged {
		messageMetaSet = removeSystemMetadata(messageMetaSet)
	}

	tx, isDBTransaction := db.(bun.Tx)
	if !isDBTransaction {
		// db is not already a transaction, so begin one
		if tx, err = db.BeginTx(ctx, &sql.TxOptions{}); err != nil {
			return NewStorageError("failed to begin transaction", err)
		}
		defer rollbackOnError(tx)
	}

	for i := range messageMetaSet {
		err := putMessageMetadataTx(ctx, tx, sessionID, &messageMetaSet[i])
		if err != nil {
			// defer will roll back the transaction
			return NewStorageError("failed to put message metadata", err)
		}
	}

	if !isDBTransaction {
		if err = tx.Commit(); err != nil {
			return NewStorageError("failed to commit transaction", err)
		}
	}

	return nil
}

// removeSystemMetadata removes the top-level `system` key from the metadata. This
// is used to prevent unprivileged callers from storing metadata in the `system` tree.
func removeSystemMetadata(metadata []models.MessageMetadata) []models.MessageMetadata {
	filteredMessageMetadata := make([]models.MessageMetadata, 0)

	for _, m := range metadata {
		if m.Key != "system" && !strings.HasPrefix(m.Key, "system.") {
			delete(m.Metadata, "system")
			filteredMessageMetadata = append(filteredMessageMetadata, m)
		}
	}
	return filteredMessageMetadata
}

func putMessageMetadataTx(
	ctx context.Context,
	tx bun.Tx,
	sessionID string,
	messageMetadata *models.MessageMetadata,
) error {
	// TODO: simplify all of this by getting `jsonb_set` working in bun

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
	if len(keyPath) == 0 || (len(keyPath) == 1 && keyPath[0] == "") {
		for k, v := range metadata.(map[string]interface{}) {
			value[k] = v
		}
		return
	}

	for _, key := range keyPath[:len(keyPath)-1] {
		value = findOrCreateMetadataMap(value, key)
	}
	value[keyPath[len(keyPath)-1]] = metadata
}
