package postgres

import (
	"context"
	"fmt"

	"github.com/uptrace/bun"
)

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
