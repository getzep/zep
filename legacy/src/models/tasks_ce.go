
package models

type TaskPublisher interface {
	TaskPublisherCommon
}

type MessageTask struct {
	MessageTaskCommon
}

type TaskState struct {
	TaskStateCommon
}
