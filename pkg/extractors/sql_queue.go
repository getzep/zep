package extractors

import (
	"database/sql"
	"time"

	"github.com/ThreeDotsLabs/watermill"
	wsql "github.com/ThreeDotsLabs/watermill-sql/v2/pkg/sql"
	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/ThreeDotsLabs/watermill/message/router/middleware"
	"github.com/ThreeDotsLabs/watermill/message/router/plugin"
	"github.com/getzep/zep/pkg/models"
	"github.com/sirupsen/logrus"
)

func NewSQLQueueRouter(logger watermill.LoggerAdapter) (*message.Router, error) {
	// Create a new router
	cfg := message.RouterConfig{}
	router, err := message.NewRouter(cfg, logger)
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
			Logger:          logger,
		}.Middleware,

		// Recoverer handles panics from handlers.
		// In this case, it passes them as errors to the Retry middleware.
		middleware.Recoverer,
	)
	return router, nil
}

func NewSQLQueuePublisher(db *sql.DB, logger watermill.LoggerAdapter) (message.Publisher, error) {
	return wsql.NewPublisher(
		db,
		wsql.PublisherConfig{
			SchemaAdapter:        wsql.DefaultPostgreSQLSchema{},
			AutoInitializeSchema: true,
		},
		logger,
	)
}

func NewSQLQueueSubscriber(db *sql.DB, logger watermill.LoggerAdapter) (message.Subscriber, error) {
	return wsql.NewSubscriber(
		db,
		wsql.SubscriberConfig{
			SchemaAdapter:  wsql.DefaultPostgreSQLSchema{},
			OffsetsAdapter: &wsql.DefaultPostgreSQLOffsetsAdapter{},
		},
		logger,
	)
}

func NewLogHandler(logger *logrus.Logger) *LogHandler {
	return &LogHandler{log: logger}
}

type LogHandler struct {
	log *logrus.Logger
}

func (l *LogHandler) Handler(msg *message.Message) ([]*message.Message, error) {
	l.log.Info("LogHandler received message", msg.UUID)

	msg = message.NewMessage(watermill.NewUUID(), []byte("message produced by LogHandler"))
	return message.Messages{msg}, nil
}

// TODO: Where to close this?
func NewPostgresConnForQueue(appState *models.AppState) (*sql.DB, error) {
	db, err := sql.Open("pgx", appState.Config.Store.Postgres.DSN)
	if err != nil {
		return nil, err
	}

	return db, nil
}
