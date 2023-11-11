package tasks

import (
	"database/sql"
	"time"

	wotel "github.com/voi-oss/watermill-opentelemetry/pkg/opentelemetry"

	"github.com/ThreeDotsLabs/watermill"
	wsql "github.com/ThreeDotsLabs/watermill-sql/v2/pkg/sql"
	"github.com/ThreeDotsLabs/watermill/message"
)

const SQLSubscriberPollInterval = 500 * time.Millisecond

type SQLSchema struct {
	wsql.DefaultPostgreSQLSchema
}

func (s SQLSchema) SubscribeIsolationLevel() sql.IsolationLevel {
	// Override the default per the repo comment.
	// https://github.com/ThreeDotsLabs/watermill-sql/blob/b6c85087b1cbd92a081186077ba1f8145ea6422e/pkg/sql/schema_adapter_postgresql.go#L143
	return sql.LevelRepeatableRead
}

func NewSQLQueuePublisher(db *sql.DB, logger watermill.LoggerAdapter) (message.Publisher, error) {
	p, err := wsql.NewPublisher(
		db,
		wsql.PublisherConfig{
			SchemaAdapter:        SQLSchema{},
			AutoInitializeSchema: true,
		},
		logger,
	)
	if err != nil {
		return nil, err
	}
	return wotel.NewPublisherDecorator(p), nil
}

func NewSQLQueueSubscriber(db *sql.DB, logger watermill.LoggerAdapter) (message.Subscriber, error) {
	return wsql.NewSubscriber(
		db,
		wsql.SubscriberConfig{
			SchemaAdapter:    SQLSchema{},
			OffsetsAdapter:   &wsql.DefaultPostgreSQLOffsetsAdapter{},
			InitializeSchema: true,
			PollInterval:     SQLSubscriberPollInterval,
		},
		logger,
	)
}
