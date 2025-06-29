package pg

import (
	"context"
	"database/sql"
	"fmt"
	"runtime"
	"time"

	"github.com/uptrace/bun"
	"github.com/uptrace/bun/dialect/pgdialect"
	"github.com/uptrace/bun/driver/pgdriver"
	"github.com/uptrace/bun/extra/bunotel"

	"github.com/getzep/zep/lib/config"
	"github.com/getzep/zep/lib/logger"
)

var maxOpenConns = 4 * runtime.GOMAXPROCS(0)

type Connection struct {
	*bun.DB
}

// NewConnection creates a new database connection and will panic if the connection fails.
// Assumed to be called at startup so panicking is ok as it will prevent the app from starting.
func NewConnection() Connection {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if config.Postgres().DSN() == "" {
		panic(fmt.Errorf("missing postgres DSN"))
	}

	sqldb := sql.OpenDB(
		pgdriver.NewConnector(
			pgdriver.WithDSN(config.Postgres().DSN()),
			pgdriver.WithReadTimeout(15*time.Second),
			pgdriver.WithWriteTimeout(15*time.Second),
		),
	)
	sqldb.SetMaxOpenConns(maxOpenConns)
	sqldb.SetMaxIdleConns(maxOpenConns)

	db := bun.NewDB(sqldb, pgdialect.New())
	db.AddQueryHook(bunotel.NewQueryHook(bunotel.WithDBName("zep")))

	// Enable pgvector extension
	err := enablePgVectorExtension(ctx, db)
	if err != nil {
		panic(fmt.Errorf("error enabling pgvector extension: %w", err))
	}

	if logger.GetLogLevel() == logger.DebugLevel {
		enableDebugLogging(db)
	}

	return Connection{
		DB: db,
	}
}

func enableDebugLogging(db *bun.DB) {
	db.AddQueryHook(logger.NewQueryHook(logger.QueryHookOptions{
		LogSlow:    time.Second,
		QueryLevel: logger.DebugLevel,
		ErrorLevel: logger.ErrorLevel,
		SlowLevel:  logger.WarnLevel,
	}))
}

func enablePgVectorExtension(ctx context.Context, db *bun.DB) error {
	// Create pgvector extension in 'extensions' schema if it does not exist
	_, err := db.ExecContext(ctx, "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA PUBLIC;")
	if err != nil {
		return fmt.Errorf("error creating pgvector extension: %w", err)
	}

	// if this is an upgrade, we may need to update the pgvector extension
	// this is a no-op if the extension is already up to date
	// if this fails, Zep may not have rights to update extensions.
	// this is not an issue if running on a managed service.
	_, err = db.ExecContext(ctx, "ALTER EXTENSION vector UPDATE")
	if err != nil {
		// TODO should this just panic or at last return the error?
		logger.Error(
			"error updating pgvector extension: %s. this may happen if running on a managed service without rights to update extensions.",
			"error", err,
		)

		return nil
	}

	return nil
}
