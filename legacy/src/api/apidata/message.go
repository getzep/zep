package apidata

import (
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep/models"
)

func MessageListTransformer(messages []models.Message) []Message {
	m := make([]Message, len(messages))
	for i, message := range messages {
		m[i] = MessageTransformer(message)
	}

	return m
}

func MessageTransformerPtr(message models.Message) *Message {
	msg := MessageTransformer(message)

	return &msg
}

func MessageTransformer(message models.Message) Message {
	return Message{
		UUID:       message.UUID,
		CreatedAt:  message.CreatedAt,
		UpdatedAt:  message.UpdatedAt,
		Role:       message.Role,
		RoleType:   RoleType(message.RoleType),
		Content:    message.Content,
		Metadata:   message.Metadata,
		TokenCount: message.TokenCount,
	}
}
func MessagesToModelMessagesTransformer(messages []Message) []models.Message {
	m := make([]models.Message, len(messages))
	for i, message := range messages {
		m[i] = MessageToModelMessageTransformer(message)
	}

	return m
}
func MessageToModelMessageTransformer(message Message) models.Message {
	return models.Message{
		UUID:       message.UUID,
		CreatedAt:  message.CreatedAt,
		UpdatedAt:  message.UpdatedAt,
		Role:       message.Role,
		RoleType:   models.RoleType(message.RoleType),
		Content:    message.Content,
		Metadata:   message.Metadata,
		TokenCount: message.TokenCount,
	}
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

type RoleType string

const (
	NoRole        RoleType = "norole"
	SystemRole    RoleType = "system"
	AssistantRole RoleType = "assistant"
	UserRole      RoleType = "user"
	FunctionRole  RoleType = "function"
	ToolRole      RoleType = "tool"
)

type MessageListResponse struct {
	// A list of message objects.
	Messages []Message `json:"messages"`
	// The total number of messages.
	TotalCount int `json:"total_count"`
	// The number of messages returned.
	RowCount int `json:"row_count"`
}
