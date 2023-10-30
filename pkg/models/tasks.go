package models

import (
	"context"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/google/uuid"
)

type TaskTopic string

const (
	MessageSummarizerTopic      TaskTopic = "message_summarizer"
	MessageEmbedderTopic        TaskTopic = "message_embedder"
	MessageNerTopic             TaskTopic = "message_ner"
	MessageIntentTopic          TaskTopic = "message_intent"
	MessageTokenCountTopic      TaskTopic = "message_token_count"
	DocumentEmbedderTopic       TaskTopic = "document_embedder"
	MessageSummaryEmbedderTopic TaskTopic = "message_summary_embedder"
	MessageSummaryNERTopic      TaskTopic = "message_summary_ner"
)

type Task interface {
	Execute(ctx context.Context, event *message.Message) error
	HandleError(err error)
}

type TaskRouter interface {
	Run(ctx context.Context) error
	AddTask(ctx context.Context, name string, taskType TaskTopic, task Task)
	RunHandlers(ctx context.Context) error
	IsRunning() bool
	Close() error
}

type TaskPublisher interface {
	Publish(taskType TaskTopic, metadata map[string]string, payload any) error
	PublishMessage(metadata map[string]string, payload []MessageTask) error
	Close() error
}

type MessageTask struct {
	UUID uuid.UUID `json:"uuid"`
}

type MessageSummaryTask struct {
	UUID uuid.UUID `json:"uuid"`
}
