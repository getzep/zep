package migrations

import (
	"context"
	"embed"
	"fmt"

	"github.com/uptrace/bun/migrate"

	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/pg"
)

//go:embed *.sql
var sqlMigrations embed.FS

func Migrate(ctx context.Context, db pg.Connection, schemaName string) error {
	migrations := migrate.NewMigrations()

	if err := migrations.Discover(sqlMigrations); err != nil {
		return fmt.Errorf("failed to discover migrations: %w", err)
	}

	// Set the search path to the current schema.
	if _, err := db.Exec(`SET search_path TO ?`, schemaName); err != nil {
		return fmt.Errorf("failed to set search path: %w", err)
	}

	migrator := migrate.NewMigrator(db.DB, migrations)

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
		_, rollBackErr := migrator.Rollback(ctx)
		if rollBackErr != nil {
			panic(
				fmt.Errorf("failed to apply migrations and rollback was unsuccessful: %v %w", err, rollBackErr),
			)
		}

		panic(fmt.Errorf("failed to apply migrations. rolled back successfully. %w", err))
	}

	if group.IsZero() {
		logger.Info("there are no new migrations to run (database is up to date)")
		return nil
	}

	logger.Info("migration complete", "group", group)

	return nil
}
