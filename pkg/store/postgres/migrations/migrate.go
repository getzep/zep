package migrations

import (
	"context"
	"embed"
	"fmt"

	"github.com/getzep/zep/internal"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/migrate"
)

var log = internal.GetLogger()

//go:embed *.sql
var sqlMigrations embed.FS

func Migrate(ctx context.Context, db *bun.DB) error {
	migrations := migrate.NewMigrations()

	if err := migrations.Discover(sqlMigrations); err != nil {
		return fmt.Errorf("failed to discover migrations: %w", err)
	}

	migrator := migrate.NewMigrator(db, migrations)

	if err := migrator.Init(ctx); err != nil {
		return fmt.Errorf("failed to init migrator: %w", err)
	}

	if err := migrator.Lock(ctx); err != nil {
		return fmt.Errorf("failed to lock migrator: %w", err)
	}
	defer func(migrator *migrate.Migrator, ctx context.Context) {
		err := migrator.Unlock(ctx)
		if err != nil {
			panic(fmt.Errorf("failed to unlock migrator: %w", err))
		}
	}(migrator, ctx)

	group, err := migrator.Migrate(ctx)
	if err != nil {
		defer func(migrator *migrate.Migrator, ctx context.Context) {
			err := migrator.Unlock(ctx)
			if err != nil {
				panic(fmt.Errorf("failed to unlock migrator: %w", err))
			}
		}(migrator, ctx)
		_, err := migrator.Rollback(ctx)
		if err != nil {
			panic(fmt.Errorf("failed to apply migrations and rollback was unsuccessful: %w", err))
		}

		panic(fmt.Errorf("failed to apply migrations. rolled back successfully. %w", err))
	}

	if group.IsZero() {
		log.Info("there are no new migrations to run (database is up to date)")
		return nil
	}
	log.Infof("migrated to %s\n", group)

	return nil
}
