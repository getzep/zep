
package models

import "github.com/google/uuid"

type RequestState struct {
	RequestStateCommon
}

func (rs *RequestState) GetTaskState(itemUUID uuid.UUID, projectUUIDOverride ...uuid.UUID) TaskState {
	projectUUID := rs.ProjectUUID
	if len(projectUUIDOverride) > 0 {
		projectUUID = projectUUIDOverride[0]
	}

	return TaskState{
		TaskStateCommon: TaskStateCommon{
			UUID:        itemUUID,
			ProjectUUID: projectUUID,
			SchemaName:  rs.SchemaName,
		},
	}
}
