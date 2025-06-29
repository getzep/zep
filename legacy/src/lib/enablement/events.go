package enablement

type Event string

func (t Event) String() string {
	return string(t)
}

const (
	Event_CreateUser          Event = "user_create"
	Event_DeleteUser          Event = "user_delete"
	Event_CreateAPIKey        Event = "api_key_create"
	Event_CreateAccountMember Event = "account_create_member"
	Event_CreateProject       Event = "project_create"
	Event_DeleteProject       Event = "project_delete"
	Event_DataExtractor       Event = "sde_call"
	Event_CreateSession       Event = "session_create"
	Event_CreateMemoryMessage Event = "memory_create_message"
)
