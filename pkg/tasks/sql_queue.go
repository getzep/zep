package tasks

import (
	"database/sql"

	"github.com/ThreeDotsLabs/watermill"
	wsql "github.com/ThreeDotsLabs/watermill-sql/v2/pkg/sql"
	"github.com/ThreeDotsLabs/watermill/message"
)

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
			SchemaAdapter:    wsql.DefaultPostgreSQLSchema{},
			OffsetsAdapter:   &wsql.DefaultPostgreSQLOffsetsAdapter{},
			InitializeSchema: true,
		},
		logger,
	)
}
