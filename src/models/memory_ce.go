
package models

type Memory struct {
	MemoryCommon
}

type MemoryFilterOptions struct{}

func (m *Message) MessageTask(rs *RequestState, memory Memory) MessageTask {
	return MessageTask{
		MessageTaskCommon: MessageTaskCommon{
			TaskState: rs.GetTaskState(m.UUID),
		},
	}
}
