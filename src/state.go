package main

import (
	"context"

	"github.com/getzep/zep/lib/pg"
	"github.com/getzep/zep/lib/telemetry"
	"github.com/getzep/zep/models"
)

func newAppState() *models.AppState {
	as := &models.AppState{}

	as.DB = pg.NewConnection()

	initializeDB(context.Background(), as)

	telemetry.Setup()

	setup(as)

	return as
}
