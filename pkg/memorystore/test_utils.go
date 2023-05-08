package memorystore

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	"github.com/uptrace/bun"
)

func CleanDB(t *testing.T, db *bun.DB) {
	_, err := db.NewDropTable().
		Model(&PgSession{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)

	_, err = db.NewDropTable().
		Model(&PgMessageStore{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&PgMessageVectorStore{}).
		IfExists().
		Cascade().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&PgSummaryStore{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
}
