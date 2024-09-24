
package store

import (
	"context"
	"fmt"

	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/pg"
)

// purgeDeletedResources purges deleted resources from the database. It will be called when a user or a session is deleted to hard delete the soft deleter resources.
// On cloud a PurgeDeletedResources task is used instead
func purgeDeletedResources(ctx context.Context, db pg.Connection) error {
	logger.Debug("purging memory store")

	for _, schema := range messageTableList {
		logger.Debug("purging schema", schema)
		_, err := db.NewDelete().
			Model(schema).
			WhereDeleted().
			ForceDelete().
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error purging rows from %T: %w", schema, err)
		}
	}

	// Vacuum database post-purge. This is avoids issues with HNSW indexes
	// after deleting a large number of rows.
	// https://github.com/pgvector/pgvector/issues/244
	_, err := db.ExecContext(ctx, "VACUUM ANALYZE")
	if err != nil {
		return fmt.Errorf("error vacuuming database: %w", err)
	}

	logger.Info("completed purging store")

	return nil
}
