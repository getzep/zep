package queue

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/ThreeDotsLabs/watermill"
	"github.com/ThreeDotsLabs/watermill/message/router/middleware"

	"github.com/ThreeDotsLabs/watermill/message/router/plugin"

	"github.com/ThreeDotsLabs/watermill/message"

	wsql "github.com/ThreeDotsLabs/watermill-sql/v2/pkg/sql"
	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
	wla "github.com/ma-hartma/watermill-logrus-adapter"
)

var log = internal.GetLogger()
var wlogger = wla.NewLogrusLogger(log)

// TODO: make these configurable
const RouterThrottleConcurrency = 10
const MaxRouterRetries = 3
const RouterInitialRetryInterval = time.Millisecond * 100

func NewQueue(name string) models.Queue {
	return models.Queue{
		Name:         name,
		ConsumeTopic: name + "_consume",
		PublishTopic: name + "_publish",
	}
}

func NewEmbeddingRouter(appState *models.AppState) (*message.Router, error) {
	queue := appState.Queues["embeddings"]

	router, err := message.NewRouter(message.RouterConfig{}, wlogger)
	if err != nil {
		return nil, fmt.Errorf("failed to create router: %w", err)
	}
	router.AddPlugin(plugin.SignalsHandler)
	router.AddMiddleware(
		// CorrelationID will copy the correlation id from the incoming message's metadata to the produced messages
		middleware.CorrelationID,
		// The handler function is retried if it returns an error.
		// After MaxRetries, the message is Nacked and it's up to the PubSub to resend it.
		middleware.Retry{
			MaxRetries:      MaxRouterRetries,
			InitialInterval: RouterInitialRetryInterval,
			Logger:          wlogger,
		}.Middleware,
		// Recoverer handles panics from handlers.
		// In this case, it passes them as errors to the Retry middleware.
		middleware.Recoverer,
		// Throttle provides a middleware that limits the amount of messages processed per unit of time.
		// This may be done e.g. to prevent excessive load caused by running a handler on a long queue of unprocessed
		middleware.NewThrottle(RouterThrottleConcurrency, time.Second).Middleware,
	)

	subscriber, err := NewSQLSubscriber(appState.SqlDB)
	if err != nil {
		return nil, fmt.Errorf("failed to create subscriber: %w", err)
	}
	publisher, err := NewSQLPublisher(appState.SqlDB)
	if err != nil {
		return nil, fmt.Errorf("failed to create publisher: %w", err)
	}

	queue.Subscriber = subscriber
	queue.Publisher = publisher

	router.AddHandler(
		"handler_1",        // handler name, must be unique
		queue.PublishTopic, // topic from which messages should be consumed
		subscriber,
		queue.ConsumeTopic, // topic to which messages should be published
		publisher,
		EmbeddingHandler(), // handler function
	)

	return router, nil
}

func NewSQLSubscriber(
	db *sql.DB,
) (*wsql.Subscriber, error) {
	return wsql.NewSubscriber(
		db,
		wsql.SubscriberConfig{
			SchemaAdapter:    wsql.DefaultPostgreSQLSchema{},
			OffsetsAdapter:   wsql.DefaultPostgreSQLOffsetsAdapter{},
			InitializeSchema: true,
		},
		wlogger,
	)
}

func NewSQLPublisher(
	db *sql.DB,
) (*wsql.Publisher, error) {
	return wsql.NewPublisher(
		db,
		wsql.PublisherConfig{
			SchemaAdapter:        wsql.DefaultPostgreSQLSchema{},
			AutoInitializeSchema: true,
		},
		wlogger,
	)
}

func EmbeddingHandler() func(msg *message.Message) ([]*message.Message, error) {
	return func(msg *message.Message) ([]*message.Message, error) {
		consumedPayload := models.DocumentEmbeddingEvent{}
		err := json.Unmarshal(msg.Payload, &consumedPayload)
		if err != nil {
			return nil, err
		}

		log.Debugf("received event %+v", consumedPayload)

		newPayload, err := json.Marshal(models.DocumentEmbeddingEventProcessed{
			UUID:        consumedPayload.UUID,
			ProcessedAt: time.Now(),
			Embedding:   []float32{0.1, 0.2, 0.3},
		})
		if err != nil {
			return nil, err
		}

		newMessage := message.NewMessage(watermill.NewUUID(), newPayload)

		log.Debugf("publishing event %+v", newMessage)

		return []*message.Message{newMessage}, nil
	}
}
