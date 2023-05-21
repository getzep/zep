package memorystore

import (
	"context"
	"fmt"

	"github.com/uptrace/bun"
)

// deleteSession deletes a session from the memory store. This is a soft delete.
// TODO: This is ugly. Determine why bun's cascading deletes aren't working
func deleteSession(ctx context.Context, db *bun.DB, sessionID string) error {
	log.Debugf("deleting from memory store for session %s", sessionID)
	schemas := []bun.BeforeCreateTableHook{
		&PgMessageVectorStore{},
		&PgSummaryStore{},
		&PgMessageStore{},
		&PgSession{},
	}
	for _, schema := range schemas {
		log.Debugf("deleting session %s from schema %v", sessionID, schema)
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
