package tasks

import (
	"context"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/store/postgres"

	"github.com/stretchr/testify/assert"
)

func TestRunTaskRouter(t *testing.T) {
	ctx, done := context.WithTimeout(testCtx, 5*time.Second)
	defer done()

	db, err := postgres.NewPostgresConnForQueue(appState)
	assert.NoError(t, err, "failed to connect to database")

	// run the router
	RunTaskRouter(ctx, appState, db)

	// check that the router is configured
	assert.NotNil(t, appState.TaskRouter, "task router is nil")
	assert.NotNil(t, appState.TaskPublisher, "task publisher is nil")

	// wait for router startup
	timeout := time.After(10 * time.Second)
	tick := time.Tick(500 * time.Millisecond)
	for {
		select {
		case <-timeout:
			t.Fatal("Test timed out waiting for the router to start")
		case <-tick:
			if appState.TaskRouter.IsRunning() {
				goto RouterStarted
			}
		}
	}

RouterStarted:
	err = appState.TaskRouter.Close()
	assert.NoError(t, err, "failed to close task router")
}
