
package store

import (
	"context"

	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

func (dao *messageDAO) cleanup(ctx context.Context, messageUUID uuid.UUID, tx *bun.Tx) error {
	return nil
}
