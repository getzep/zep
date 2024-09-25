package models

import (
	"context"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/google/uuid"
)

type TaskTopic string

const (
	MessageEmbedderTopic       TaskTopic = "message_embedder"
	PurgeDeletedResourcesTopic TaskTopic = "purge_deleted"
)

type Task interface {
	Execute(ctx context.Context, event *message.Message) error
	HandleError(msgId string, err error)
}

type TaskRouter interface {
	Run(ctx context.Context) error
	AddTask(ctx context.Context, name string, taskType TaskTopic, task Task, numOfSubscribers int)
	AddTaskWithMultiplePools(ctx context.Context, name string, taskType TaskTopic, task Task, numberOfPools int) error
	RunHandlers(ctx context.Context) error
	IsRunning() bool
	Close() error
}

type TaskPublisherCommon interface {
	Publish(ctx context.Context, taskType TaskTopic, metadata map[string]string, payload any) error
	PublishMessage(ctx context.Context, metadata map[string]string, payload []MessageTask) error
	Close() error
}

type MessageTaskCommon struct {
	TaskState
}

type TaskStateCommon struct {
	UUID        uuid.UUID `json:"uuid"`
	ProjectUUID uuid.UUID `json:"project_uuid"`
	SchemaName  string    `json:"schema_name"`
}

func (ts *TaskStateCommon) LogData(data ...any) []any {
	if ts.UUID != uuid.Nil {
		data = append(data, "uuid", ts.UUID)
	}

	if ts.ProjectUUID != uuid.Nil {
		data = append(data, "project_uuid", ts.ProjectUUID)
	}

	if ts.SchemaName != "" {
		data = append(data, "schema_name", ts.SchemaName)
	}

	return data
}
