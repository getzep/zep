package tasks

import (
	"context"
	"database/sql"
	"sync"
	"time"

	wotel "github.com/voi-oss/watermill-opentelemetry/pkg/opentelemetry"

	"github.com/ThreeDotsLabs/watermill"
	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/ThreeDotsLabs/watermill/message/router/middleware"
	"github.com/getzep/zep/pkg/models"
	wla "github.com/ma-hartma/watermill-logrus-adapter"
)

// TODO: Add these to config

const TaskCountThrottle = 250 // messages per second
const MaxQueueRetries = 5
const TaskTimeout = 60 // seconds

var onceRouter sync.Once

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

	// Set up a poison queue
	publisher, err := NewSQLQueuePublisher(db, wlog)
	if err != nil {
		return nil, err
	}
	poisonQueueHandler, err := middleware.PoisonQueue(publisher, "poison_queue")
	if err != nil {
		return nil, err
	}

	router.AddMiddleware(
		// Watermill opentelemetry middleware
		wotel.Trace(),

		// Throttle limits the number of messages processed per second.
		middleware.NewThrottle(TaskCountThrottle, time.Second).Middleware,

		// Recoverer handles panics from handlers.
		// In this case, it passes them as errors to the Retry middleware.
		middleware.Recoverer,

		// PoisonQueue will publish messages that failed to process after MaxRetries to the poison queue.
		poisonQueueHandler,

		// The handler function is retried if it returns an error.
		// After MaxRetries, the message is Nacked and it's up to the PubSub to resend it.
		middleware.Retry{
			MaxRetries:          MaxQueueRetries,
			InitialInterval:     1 * time.Second,
			MaxInterval:         5 * time.Second,
			Multiplier:          1.5,
			RandomizationFactor: 0.5,
			Logger:              wlog,
		}.Middleware,
	)

	return &TaskRouter{
		Router:   router,
		appState: appState,
		db:       db,
		logger:   wlog,
	}, nil
}

// AddTask adds a task handler to the router.
func (tr *TaskRouter) AddTask(
	_ context.Context,
	name string,
	taskType models.TaskTopic,
	task models.Task,
) {
	subscriber, err := NewSQLQueueSubscriber(tr.db, tr.logger)
	if err != nil {
		log.Fatalf("Failed to create subscriber for task %s: %v", taskType, err)
	}
	tr.AddNoPublisherHandler(
		name,
		string(taskType),
		subscriber,
		TaskHandler(task),
	)
}

func (tr *TaskRouter) Close() (err error) {
	defer func() {
		if dbErr := tr.db.Close(); dbErr != nil && err == nil {
			err = dbErr
		}
	}()

	if publisherErr := tr.appState.TaskPublisher.Close(); publisherErr != nil {
		err = publisherErr
	}

	if routerErr := tr.Router.Close(); routerErr != nil && err == nil {
		err = routerErr
	}

	return err
}

// TaskHandler returns a message handler function for the given task.
// Handlers are NoPublishHandlerFuncs i.e. do not publish messages.
func TaskHandler(task models.Task) message.NoPublishHandlerFunc {
	return func(msg *message.Message) error {
		log.Debugf("Handling task: %s", msg.UUID)
		err := task.Execute(msg.Context(), msg)
		if err != nil {
			task.HandleError(err)
			return err
		}
		log.Debugf("Handled task: %s", msg.UUID)
		return nil
	}
}

func RunTaskRouter(ctx context.Context, appState *models.AppState, db *sql.DB) {
	// Run once to avoid test situations where the router is initialized multiple times
	log.Debug("RunTaskRouter called")
	onceRouter.Do(func() {
		router, err := NewTaskRouter(appState, db)
		if err != nil {
			log.Fatalf("failed to create task router: %v", err)
		}

		publisher := NewTaskPublisher(db)
		Initialize(ctx, appState, router)

		appState.TaskRouter = router
		appState.TaskPublisher = publisher

		go func() {
			log.Info("running task router")
			err := router.Run(ctx)
			if err != nil {
				log.Fatalf("failed to run task router %v", err)
			}
		}()
	})
}
