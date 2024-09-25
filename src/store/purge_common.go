package store

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

// purgeDeleted hard deletes all soft deleted records from the memory store.
func purgeDeleted(ctx context.Context, db *bun.DB, schemaName string, projectUUID uuid.UUID) error {
	if schemaName == "" {
		return fmt.Errorf("schemaName cannot be empty")
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	_, err = tx.Exec("SET LOCAL search_path TO ?"+SearchPathSuffix, schemaName)
	if err != nil {
		return fmt.Errorf("error setting schema: %w", err)
	}

	// Delete all messages, message embeddings, and summaries associated with sessions
	for _, schema := range messageTableList {
		_, err := tx.NewDelete().
			Model(schema).
			WhereDeleted().
			ForceDelete().
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error purging rows from %T: %w", schema, err)
		}
	}

	// Delete user store records.
	_, err = tx.NewDelete().
		Model(&UserSchema{}).
		WhereDeleted().
		ForceDelete().
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("error purging rows from %T: %w", &UserSchema{}, err)
	}

	err = tableCleanup(ctx, &tx, schemaName, projectUUID)
	if err != nil {
		return fmt.Errorf("failed to cleanup tables: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}
