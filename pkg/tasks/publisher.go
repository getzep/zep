package tasks

import (
	"database/sql"
	"encoding/json"
	"fmt"

	"github.com/ThreeDotsLabs/watermill"
	"github.com/ThreeDotsLabs/watermill/message"
	wla "github.com/ma-hartma/watermill-logrus-adapter"
)

type TaskPublisher struct {
	publisher message.Publisher
}

func NewTaskPublisher(db *sql.DB) *TaskPublisher {
	var wlog = wla.NewLogrusLogger(log)
	publisher, err := NewSQLQueuePublisher(db, wlog)
	if err != nil {
		log.Fatalf("Failed to create task publisher: %v", err)
	}
	return &TaskPublisher{
		publisher: publisher,
	}
}

func (t *TaskPublisher) Publish(taskType string, metadata map[string]string, payload any) error {
	p, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal message: %w", err)
	}
	log.Debugf("Publishing message: %s", p)
	m := message.NewMessage(watermill.NewUUID(), p)
	m.Metadata = message.Metadata(metadata)

	err = t.publisher.Publish(taskType, m)
	if err != nil {
		return fmt.Errorf("failed to publish task message: %w", err)
	}

	return nil
}

func (t *TaskPublisher) Close() error {
	err := t.publisher.Close()
	if err != nil {
		return fmt.Errorf("failed to close task publisher: %w", err)
	}

	return nil
}
