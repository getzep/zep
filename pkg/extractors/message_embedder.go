package extractors

import (
	"database/sql"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/ThreeDotsLabs/watermill/message/router/middleware"
	"github.com/ThreeDotsLabs/watermill/message/router/plugin"
	wla "github.com/ma-hartma/watermill-logrus-adapter"
)

const MessageEmbeddingTopic = "message_embedding"

type EmbeddingRouter struct {
	*message.Router
	publisher message.Publisher
}

func NewEmbeddingRouter(db *sql.DB) (*EmbeddingRouter, error) {
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

		// Recoverer handles panics from handlers.
		// In this case, it passes them as errors to the Retry middleware.
		middleware.Recoverer,
	)

	publisher, err := NewSQLQueuePublisher(db, wlog)
	if err != nil {
		return nil, err
	}

	subscriber, err := NewSQLQueueSubscriber(db, wlog)
	if err != nil {
		return nil, err
	}

	logHandler := NewLogHandler(log)

	router.AddHandler(
		MessageEmbeddingTopic,
		MessageEmbeddingTopic,
		subscriber,
		MessageEmbeddingTopic,
		publisher,
		logHandler.Handler,
	)

	return &EmbeddingRouter{
		Router:    router,
		publisher: publisher,
	}, nil
}

func processEmbedMessages(messages <-chan *message.Message) {
	for msg := range messages {
		log.Infof("received message: %s, payload: %s", msg.UUID, string(msg.Payload))

		// we need to Acknowledge that we received and processed the message,
		// otherwise, it will be resent over and over again.
		msg.Ack()
	}
}
