package postgres

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	"github.com/uptrace/bun"
)

func CleanDB(t *testing.T, db *bun.DB) {
	_, err := db.NewDropTable().
		Model(&SessionSchema{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)

	_, err = db.NewDropTable().
		Model(&UserSchema{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)

	_, err = db.NewDropTable().
		Model(&MessageStoreSchema{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&MessageVectorStoreSchema{}).
		IfExists().
		Cascade().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&SummaryStoreSchema{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&SummaryVectorStoreSchema{}).
		IfExists().
		Cascade().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&DocumentCollectionSchema{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
}
