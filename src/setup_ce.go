
package main

import (
	"context"
	"log"

	"github.com/getzep/zep/lib/config"
	"github.com/getzep/zep/lib/graphiti"
	"github.com/getzep/zep/models"
	"github.com/getzep/zep/store"
)

func setup(as *models.AppState) {
	graphiti.Setup()
}

func gracefulShutdown() {}

func initializeDB(ctx context.Context, as *models.AppState) {
	err := store.MigrateSchema(ctx, as.DB, config.Postgres().SchemaName)
	if err != nil {
		log.Fatalf("Failed to migrate schema: %v", err) //nolint:revive // this is only called from main
	}
}
