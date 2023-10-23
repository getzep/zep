package tasks

import (
	"context"
	"database/sql"
	"time"

	"github.com/ThreeDotsLabs/watermill"
	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/ThreeDotsLabs/watermill/message/router/middleware"
	"github.com/ThreeDotsLabs/watermill/message/router/plugin"
	"github.com/getzep/zep/pkg/models"
	"github.com/sony/gobreaker"

	wla "github.com/ma-hartma/watermill-logrus-adapter"
)

// TaskRouter is a wrapper around watermill's Router that adds some
// functionality for managing tasks and handlers.
// TaskRouter uses a SQLQueueSubscriber for all handlers.
type TaskRouter struct {
	*message.Router
	appState    *models.AppState
	db          *sql.DB
	logger      watermill.LoggerAdapter
	Subscribers map[string]message.Subscriber
}

// NewTaskRouter creates a new TaskRouter. Note that db should not be a bun.DB instance
// as bun runs at an isolation level that is incompatible with watermill's SQLQueueSubscriber.
func NewTaskRouter(appState *models.AppState, db *sql.DB) (*TaskRouter, error) {
	var wlog = wla.NewLogrusLogger(log)

	// Create a new router
	cfg := message.RouterConfig{}
	router, err := message.NewRouter(cfg, wlog)
	if err != nil {
		return nil, err
	}

	// SignalsHandler will gracefully shutdown Router when SIGTERM is received.
	// You can also close the router by just calling `r.Close()`.
	router.AddPlugin(plugin.SignalsHandler)

	// Router level middleware are executed for every message sent to the router
	router.AddMiddleware(
		// CorrelationID will copy the correlation id from the incoming message's metadata to the produced messages
		middleware.CorrelationID,

		// The handler function is retried if it returns an error.
		// After MaxRetries, the message is Nacked and it's up to the PubSub to resend it.
		middleware.Retry{
			MaxRetries:      3,
			InitialInterval: time.Millisecond * 100,
			Logger:          wlog,
		}.Middleware,

		middleware.NewCircuitBreaker(gobreaker.Settings{
			Name:        "task_router_circuit_breaker",
			MaxRequests: 1,
			Interval:    time.Second * 5,
			Timeout:     time.Second * 20,
		}).Middleware,

		middleware.NewThrottle(5, time.Second).Middleware,

		// Recoverer handles panics from handlers.
		// In this case, it passes them as errors to the Retry middleware.
		middleware.Recoverer,
	)

	return &TaskRouter{
		Router:   router,
		appState: appState,
		db:       db,
		logger:   wlog,
	}, nil
}

// AddTask adds a task handler to the router.
func (tr *TaskRouter) AddTask(ctx context.Context, name, taskType string, task models.Task) {
	s, err := NewSQLQueueSubscriber(tr.db, tr.logger)
	if err != nil {
		log.Fatalf("Failed to create subscriber for task %s: %v", taskType, err)
	}
	tr.AddNoPublisherHandler(
		name,
		taskType,
		s,
		TaskHandler(task),
	)
}

// TaskHandler returns a message handler function for the given task.
// Handlers are NoPublishHandlerFuncs i.e. do not publish messages.
func TaskHandler(task models.Task) message.NoPublishHandlerFunc {
	return func(msg *message.Message) error {
		err := task.Execute(msg.Context(), msg)
		if err != nil {
			task.HandleError(err)
			return err
		}
		return nil
	}
}
