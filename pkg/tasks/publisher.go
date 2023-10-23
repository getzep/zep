package tasks

import (
	"database/sql"
	"encoding/json"
	"fmt"

	"github.com/ThreeDotsLabs/watermill"
	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/models"
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

// Publish publishes a message to the given topic. Payload must be a struct that can be marshalled to JSON.
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

// PublishMessage publishes a slice of Messages to all Message topics.
func (t *TaskPublisher) PublishMessage(metadata map[string]string, payload []models.Message) error {
	var messageTopics = []string{
		"message_summarizer",
		"message_embedder",
		"message_ner",
	}

	for _, topic := range messageTopics {
		err := t.Publish(topic, metadata, payload)
		if err != nil {
			return fmt.Errorf("failed to publish message: %w", err)
		}
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
