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

//type failTask struct {
//	appState *models.AppState
//}
//
//func (n *failTask) Execute(
//	_ context.Context,
//	_ *message.Message,
//) error {
//	return errors.New("failTask failed")
//}
//
//func (n *failTask) HandleError(err error) {
//	log.Errorf("failTask error: %s", err.Error())
//}
//
//func TestFailTaskExecution(t *testing.T) {
//	ctx, done := context.WithTimeout(testCtx, 30*time.Second)
//	defer done()
//
//	db, err := postgres.NewPostgresConnForQueue(appState)
//	assert.NoError(t, err, "failed to connect to database")
//
//	router, err := NewTaskRouter(appState, db)
//	assert.NoError(t, err, "failed to create task router")
//
//	publisher := NewTaskPublisher(db)
//	appState.TaskRouter = router
//	appState.TaskPublisher = publisher
//
//	task := &failTask{appState: appState}
//	// add the task to the router
//	router.AddTask(ctx, "failTask", "failTask", task)
//
//	go func() {
//		err := router.Run(ctx)
//		assert.NoError(t, err, "failed to run task router")
//	}()
//
//	// publish a message to the router
//	err = publisher.Publish("failTask", map[string]string{}, map[string]any{})
//	assert.NoError(t, err, "failed to publish message to router")
//
//	time.Sleep(30 * time.Second)
//
//	err = appState.TaskRouter.Close()
//	assert.NoError(t, err, "failed to close task router")
//}
