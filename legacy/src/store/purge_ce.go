
package store

import (
	"context"

	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

func tableCleanup(ctx context.Context, tx *bun.Tx, schemaName string, projectUUID uuid.UUID) error {
	return nil
}
