package postgres

import (
	"context"
	"fmt"

	"github.com/uptrace/bun"
)

// deleteSession deletes a session from the memory store. This is a soft Delete.
// Note: soft_deletes don't trigger cascade deletes, so we need to Delete all
// related records manually.
func deleteSession(ctx context.Context, db *bun.DB, sessionID string) error {
	log.Debugf("deleting from memory store for session %s", sessionID)

	for _, schema := range messageTableList {
		log.Debugf("deleting session %s from schema %T", sessionID, schema)
		_, err := db.NewDelete().
			Model(schema).
			Where("session_id = ?", sessionID).
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error deleting rows from %T: %w", schema, err)
		}
	}
	log.Debugf("completed deleting session %s", sessionID)

	return nil
}

// purgeDeleted hard deletes all soft deleted records from the memory store.
func purgeDeleted(ctx context.Context, db *bun.DB) error {
	log.Debugf("purging memory store")

	for _, schema := range messageTableList {
		log.Debugf("purging schema %T", schema)
		_, err := db.NewDelete().
			Model(schema).
			WhereDeleted().
			ForceDelete().
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error purging rows from %T: %w", schema, err)
		}
	}
	log.Info("completed purging memory store")

	return nil
}
