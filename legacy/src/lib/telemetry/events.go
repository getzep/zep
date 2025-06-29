package telemetry

type Event string

func (t Event) String() string {
	return string(t)
}

const (
	Event_CreateUser          Event = "user_create"
	Event_DeleteUser          Event = "user_delete"
	Event_CreateFacts         Event = "facts_create"
	Event_CreateMemoryMessage Event = "memory_create_message"
	Event_GetMemory           Event = "memory_get"
	Event_CreateSession       Event = "session_create"
	Event_DeleteSession       Event = "session_delete"
	Event_SearchSessions      Event = "sessions_search"

	Event_CEStart Event = "ce_start"
	Event_CEStop  Event = "ce_stop"
)
