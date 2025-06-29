package models

import (
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

type RoleType string

const (
	NoRole        RoleType = "norole"
	SystemRole    RoleType = "system"
	AssistantRole RoleType = "assistant"
	UserRole      RoleType = "user"
	FunctionRole  RoleType = "function"
	ToolRole      RoleType = "tool"
)

var validRoleTypes = map[string]RoleType{
	string(NoRole):        NoRole,
	string(SystemRole):    SystemRole,
	string(AssistantRole): AssistantRole,
	string(UserRole):      UserRole,
	string(FunctionRole):  FunctionRole,
	string(ToolRole):      ToolRole,
}

func (rt *RoleType) UnmarshalJSON(b []byte) error {
	str := strings.Trim(string(b), "\"")

	if str == "" {
		*rt = NoRole
		return nil
	}

	value, ok := validRoleTypes[str]
	if !ok {
		return fmt.Errorf("invalid RoleType: %v", str)
	}

	*rt = value
	return nil
}

func (rt RoleType) MarshalJSON() ([]byte, error) {
	return []byte(fmt.Sprintf("%q", rt)), nil
}

// Message Represents a message in a conversation.
type Message struct {
	// The unique identifier of the message.
	UUID uuid.UUID `json:"uuid"`
	// The timestamp of when the message was created.
	CreatedAt time.Time `json:"created_at"`
	// The timestamp of when the message was last updated.
	UpdatedAt time.Time `json:"updated_at"`
	// The role of the sender of the message (e.g., "user", "assistant").
	Role string `json:"role"`
	// The type of the role (e.g., "user", "system").
	RoleType RoleType `json:"role_type,omitempty"`
	// The content of the message.
	Content string `json:"content"`
	// The metadata associated with the message.
	Metadata map[string]any `json:"metadata,omitempty"`
	// The number of tokens in the message.
	TokenCount int `json:"token_count"`
}

type MessageMetadataUpdate struct {
	// The metadata to update
	Metadata map[string]any `json:"metadata" validate:"required"`
}

type MessageListResponse struct {
	// A list of message objects.
	Messages []Message `json:"messages"`
	// The total number of messages.
	TotalCount int `json:"total_count"`
	// The number of messages returned.
	RowCount int `json:"row_count"`
}

type MemoryCommon struct {
	// A list of message objects, where each message contains a role and content.
	Messages      []Message `json:"messages"`
	RelevantFacts []Fact    `json:"relevant_facts"`
	// A dictionary containing metadata associated with the memory.
	Metadata map[string]any `json:"metadata,omitempty"`
}

type MemoryFilterOption = FilterOption[MemoryFilterOptions]
