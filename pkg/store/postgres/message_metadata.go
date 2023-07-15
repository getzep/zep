package postgres

import (
	"context"
	"database/sql"

	"github.com/jinzhu/copier"

	"dario.cat/mergo"

	"github.com/uptrace/bun"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store"
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
	messages []models.Message,
	isPrivileged bool,
) ([]models.Message, error) {
	var tx bun.Tx
	var err error

	// remove the top-level `system` key from the metadata if the caller is not privileged
	if !isPrivileged {
		removeSystemMetadata(messages)
	}

	// Are we already running in a transaction?
	tx, isDBTransaction := db.(bun.Tx)
	if !isDBTransaction {
		// db is not already a transaction, so begin one
		if tx, err = db.BeginTx(ctx, &sql.TxOptions{}); err != nil {
			return nil, store.NewStorageError("failed to begin transaction", err)
		}
		defer rollbackOnError(tx)
	}

	for i := range messages {
		if len(messages[i].Metadata) == 0 {
			continue
		}
		returnedMessage, err := putMessageMetadataTx(ctx, tx, sessionID, &messages[i])
		if err != nil {
			// defer will roll back the transaction
			return nil, store.NewStorageError("failed to Create message metadata", err)
		}
		messages[i] = *returnedMessage
	}

	// if the calling function passed in a transaction, don't commit here
	if !isDBTransaction {
		if err = tx.Commit(); err != nil {
			return nil, store.NewStorageError("failed to commit transaction", err)
		}
	}

	return messages, nil
}

// removeSystemMetadata removes the top-level `system` key from the metadata. This
// is used to prevent unprivileged callers from storing metadata in the `system` tree.
func removeSystemMetadata(messages []models.Message) {
	for i := range messages {
		delete(messages[i].Metadata, "system")
	}
}

func putMessageMetadataTx(
	ctx context.Context,
	tx bun.Tx,
	sessionID string,
	message *models.Message,
) (*models.Message, error) {
	err := acquireAdvisoryXactLock(ctx, tx, sessionID+message.UUID.String())
	if err != nil {
		return nil, store.NewStorageError("failed to acquire advisory lock", err)
	}

	var retrievedMessage MessageStoreSchema
	err = tx.NewSelect().Model(&retrievedMessage).
		Column("metadata").
		Where("session_id = ? AND uuid = ?", sessionID, message.UUID).
		Scan(ctx)
	if err != nil {
		return nil, store.NewStorageError(
			"failed to retrieve existing metadata. was the session deleted?",
			err,
		)
	}

	if err := mergo.Merge(&retrievedMessage.Metadata, message.Metadata, mergo.WithOverride); err != nil {
		return nil, store.NewStorageError("failed to merge metadata", err)
	}

	retrievedMessage.UUID = message.UUID
	_, err = tx.NewUpdate().
		Model(&retrievedMessage).
		Column("metadata").
		Where("session_id = ? AND uuid = ?", sessionID, message.UUID).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, store.NewStorageError("failed to update message metadata", err)
	}

	err = copier.Copy(message, retrievedMessage)
	if err != nil {
		return nil, store.NewStorageError("Unable to copy message", err)
	}

	return message, nil
}
