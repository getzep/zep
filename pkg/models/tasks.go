package models

import (
	"context"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/google/uuid"
)

type Task interface {
	Execute(ctx context.Context, event *message.Message) error
	HandleError(err error)
}

type TaskRouter interface {
	Run(ctx context.Context) error
	AddTask(ctx context.Context, name, taskType string, task Task)
	RunHandlers(ctx context.Context) error
	IsRunning() bool
	Close() error
}

type TaskPublisher interface {
	Publish(taskType string, metadata map[string]string, payload any) error
	PublishMessage(metadata map[string]string, payload []MessageTask) error
	Close() error
}

type MessageTask struct {
	UUID uuid.UUID `json:"uuid"`
}

type MessageSummaryEmbeddingTask struct {
	UUID uuid.UUID `json:"uuid"`
}
